"""生成测试用的文档样本，避免测试依赖"碰巧留在磁盘上"的文件。

生成两类样本，对应本项目实际踩过的两个坑：

1. `text_resume.pdf` —— 可选文本的 PDF，且**带字间空格**
   （模拟简历模板 Canvas 逐字排版：`江 西 财 经大学`）。
   用来验证解析器会不会清掉这些空格、又保留英文词间空格。

2. `image_only.docx` —— 正文只有一张图的 DOCX
   （模拟"把简历导出成图片的 Word"）。用来验证系统会**明确拒绝**
   而不是切不出块、静默成功。

用法：uv run python -m app.core.make_test_fixtures
"""

import sys
from pathlib import Path

FIXTURES = Path(__file__).parent / "fixtures"

# 刻意在汉字之间插入空格，模拟 Canvas 逐字排版
SPACED_LINES = [
    "江 西 财 经大学 软 件 工 程(本科) 2024.09 - 2028.06",
    "后 端 开发：熟 悉 Python 、 FastAPI 、 RESTful API 设计",
    "大 模 型应 用：熟 悉 LangChain 、 LangGraph ，可实现 RAG 检索增强",
    "RAG 系 统：掌 握 Embedding 向 量 化 、 ChromaDB + BM25 混 合 检 索",
    "项 目 经 历：MindFlow AI 面试助手，负责后端接口与检索链路",
]


def make_text_pdf_simple(path: Path) -> None:
    """用手写最小 PDF 生成可选文本 PDF。

    为什么不生成中文 PDF：中文需要嵌入 CID 字体，手写 PDF
    会变得很复杂。而**字间空格**这个坑是"清洗规则"的问题，
    与生成方式无关 —— 因此中文场景用 `DocumentParser.normalize`
    的单元断言覆盖（见 test_document_parsing），
    这里只保证有一份"可选文本 PDF"能走通整条上传链路。
    """

    text = "Jiangxi University of Finance and Economics"
    content = (
        "BT /F1 12 Tf 50 750 Td "
        f"({text}) Tj ET"
    ).encode("ascii")

    objects = []

    def obj(body: bytes) -> bytes:
        objects.append(body)

    obj(b"<< /Type /Catalog /Pages 2 0 R >>")
    obj(b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>")
    obj(
        b"<< /Type /Page /Parent 2 0 R "
        b"/MediaBox [0 0 595 842] "
        b"/Resources << /Font << /F1 4 0 R >> >> "
        b"/Contents 5 0 R >>"
    )
    obj(
        b"<< /Type /Font /Subtype /Type1 "
        b"/BaseFont /Helvetica >>"
    )
    obj(
        b"<< /Length "
        + str(len(content)).encode()
        + b" >>\nstream\n"
        + content
        + b"\nendstream"
    )

    out = bytearray(b"%PDF-1.4\n")
    offsets = []

    for index, body in enumerate(objects, start=1):
        offsets.append(len(out))
        out += f"{index} 0 obj\n".encode() + body + b"\nendobj\n"

    xref_at = len(out)
    out += f"xref\n0 {len(objects) + 1}\n".encode()
    out += b"0000000000 65535 f \n"

    for offset in offsets:
        out += f"{offset:010d} 00000 n \n".encode()

    out += (
        f"trailer\n<< /Size {len(objects) + 1} /Root 1 0 R >>\n"
        f"startxref\n{xref_at}\n%%EOF\n"
    ).encode()

    path.write_bytes(bytes(out))


def make_image_only_docx(path: Path) -> None:
    """生成正文只有一张图的 DOCX。"""

    import docx
    from docx.shared import Inches

    # 一张极小的 PNG（1x1 像素），够触发"正文是图"的判定
    png_bytes = bytes.fromhex(
        "89504e470d0a1a0a0000000d494844520000000100000001"
        "08060000001f15c4890000000a49444154789c63000100"
        "000500010d0a2db40000000049454e44ae426082"
    )

    image_path = path.with_suffix(".png")
    image_path.write_bytes(png_bytes)

    try:
        document = docx.Document()
        # 只放一个空格段落 + 一张图：与实测那份简历结构一致
        document.add_paragraph(" ")
        document.add_picture(str(image_path), width=Inches(4))
        document.save(str(path))
    finally:
        image_path.unlink(missing_ok=True)


def main() -> int:
    FIXTURES.mkdir(parents=True, exist_ok=True)

    pdf_path = FIXTURES / "text_resume.pdf"
    make_text_pdf_simple(pdf_path)
    print(f"  已生成 {pdf_path.name}  {pdf_path.stat().st_size} B")

    docx_path = FIXTURES / "image_only.docx"
    make_image_only_docx(docx_path)
    print(f"  已生成 {docx_path.name}  {docx_path.stat().st_size} B")

    # 自检：解析结果是否符合预期
    import asyncio

    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

    from app.services.document_parser import (
        DocumentParser,
        NoExtractableText,
    )

    async def verify() -> None:
        text = await DocumentParser.parse(str(pdf_path))
        print(f"  PDF 解析出 {len(text)} 字符：{text[:60]!r}")

        try:
            await DocumentParser.parse(str(docx_path))
            print("  ✗ 图片型 DOCX 竟然解析成功了")
        except NoExtractableText:
            print("  ✓ 图片型 DOCX 正确报「无文字可提取」")

    asyncio.run(verify())
    return 0


if __name__ == "__main__":
    sys.exit(main())
