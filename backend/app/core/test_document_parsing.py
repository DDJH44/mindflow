"""DocumentParser 的解析与清洗验证（不依赖数据库、不调模型）。

覆盖两个**实际踩到的坑**：

1. PDF 字间空格：简历模板逐字排版会让文本提取成
   `江 西 财 经大学 软 件 工 程`。必须清掉这些空格，
   但**不能**碰英文词间空格（否则 `Python FastAPI`
   会被粘成 `PythonFastAPI`，检索与模型都会读错）。

2. 图片型文档：把简历导出成图片的 Word/PDF 里没有任何文字，
   必须明确报错，而不是返回空内容让用户以为上传成功。

用法：uv run python -m app.core.test_document_parsing
"""

import asyncio
import sys
from pathlib import Path

from app.services.document_parser import (
    DocumentParser,
    NoExtractableText,
    UnsupportedDocumentType,
)

FIXTURES = Path(__file__).parent / "fixtures"

results: list[tuple[str, bool]] = []


def record(label: str, ok: bool, extra: str = "") -> None:
    results.append((label, ok))
    suffix = f"  {extra}" if extra else ""
    print(f"  [{'OK  ' if ok else 'FAIL'}] {label}{suffix}")


async def main() -> int:
    normalize = DocumentParser.normalize

    # ================================================
    print()
    print("=" * 74)
    print("1. 清洗规则：汉字间空格（核心）")
    print("=" * 74)

    cases = [
        ("江 西 财 经大学 软 件 工 程", "江西财经大学软件工程"),
        ("后 端 开 发", "后端开发"),
        ("熟 悉 向 量 检 索", "熟悉向量检索"),
    ]

    for source, expected in cases:
        got = normalize(source)
        record(
            f"{source!r} → {expected!r}",
            got == expected,
            f"实际 {got!r}",
        )

    # ================================================
    print()
    print("=" * 74)
    print("2. 清洗规则：英文词间空格必须保留（同等重要）")
    print("=" * 74)

    preserved = [
        "Python FastAPI RESTful API 设计",
        "熟悉 LangChain、LangGraph 与 OpenAI 兼容接口",
        "使用 ChromaDB + BM25 混合检索",
        "PostgreSQL 存储结构化数据，Milvus 存放向量",
    ]

    for sample in preserved:
        got = normalize(sample)
        # 只要求"没有把拉丁词粘连"，不要求逐字符不变
        # （标点前的空格会被规范掉，那是期望行为）
        has_glued = any(
            pair in got
            for pair in (
                "PythonFastAPI",
                "LangChainLangGraph",
                "ChromaDBBM25",
                "PostgreSQLMilvus",
            )
        )
        record(
            f"未粘连：{sample[:40]!r}",
            not has_glued,
            f"实际 {got[:50]!r}",
        )

    # ================================================
    print()
    print("=" * 74)
    print("3. 清洗规则：换行补回与标点空格")
    print("=" * 74)

    # PDF 常丢失换行，一页挤成一行；句末标点后应补换行
    long_line = (
        "我负责后端开发。项目使用 FastAPI 构建接口。"
        "检索使用 Milvus 存放向量。切块按 500 字符。"
    )
    got = normalize(long_line)
    record(
        "句末标点后补换行",
        got.count("\n") >= 3,
        f"换行数={got.count(chr(10))}",
    )

    got = normalize("熟悉 Python 、 FastAPI ，具备能力")
    record(
        "中文标点前不留空格",
        "Python、" in got and "FastAPI，" in got,
        f"实际 {got!r}",
    )

    record(
        "零宽字符被清除",
        "\u200b" not in normalize("测\u200b试"),
    )
    record(
        "非断行空格被规整",
        "\u00a0" not in normalize("测\u00a0试"),
    )

    # ================================================
    print()
    print("=" * 74)
    print("4. 样本文件解析")
    print("=" * 74)

    pdf_path = FIXTURES / "text_resume.pdf"
    docx_path = FIXTURES / "image_only.docx"

    if not pdf_path.exists() or not docx_path.exists():
        print("      样本不存在，先运行：")
        print("      uv run python -m app.core.make_test_fixtures")
        record("样本文件存在", False)
    else:
        record("样本文件存在", True)

        text = await DocumentParser.parse(str(pdf_path))
        record(
            "可选文本 PDF 解析成功",
            "Jiangxi" in text,
            f"{len(text)} 字符",
        )

        try:
            await DocumentParser.parse(str(docx_path))
            record("图片型 DOCX 应报无文字", False, "竟然解析成功")
        except NoExtractableText as exc:
            record(
                "图片型 DOCX 报「无文字可提取」",
                True,
            )
            record(
                "提示里说明了原因与做法",
                "图片" in str(exc) and "TXT" in str(exc),
                f"{str(exc)[:60]}…",
            )

    # ================================================
    print()
    print("=" * 74)
    print("5. 不支持的扩展名")
    print("=" * 74)

    tmp = Path(r"D:\yuxi\.tmp_unsupported.xlsx")
    tmp.write_bytes(b"fake")
    try:
        await DocumentParser.parse(str(tmp))
        record("xlsx 应被拒绝", False, "竟然通过")
    except UnsupportedDocumentType as exc:
        record(
            "xlsx 被拒绝且列出支持类型",
            ".txt" in str(exc) and ".pdf" in str(exc),
            f"{str(exc)[:70]}",
        )
    finally:
        tmp.unlink(missing_ok=True)

    # ================================================
    print()
    print("=" * 74)
    passed = sum(1 for _, ok in results if ok)
    failed = [label for label, ok in results if not ok]

    print(f"通过 {passed} / {len(results)}")
    if failed:
        print("失败项:")
        for label in failed:
            print("  -", label)
        return 1

    print("全部通过")
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
