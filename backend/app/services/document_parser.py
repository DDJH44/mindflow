"""文档解析：从上传文件中提取纯文本。

支持：`.txt` / `.md` / `.pdf` / `.docx`

这里有两个**实际踩到的坑**，都不是"装个库调用一下"能解决的：

1. **PDF 的字间空格。** 简历模板常用 Canvas 逐字排版，
   导出的 PDF 提取出来是 `江 西 财 经大学 软 件 工 程`。
   直接入库会让检索按错误的词切分、也让模型读到散架的文字。
   因此需要按"空格两侧都是汉字"的规则去掉这些多余空格。
   注意**不能**把所有空格都删掉 —— 那会把
   `Python FastAPI` 粘成 `PythonFastAPI`。

2. **"图片型"文档。** 用户可能把简历导出成图片的 Word/PDF
   （实测：一份 811KB 的 docx，`document.xml` 只有 2.8KB，
   正文是一张 JPEG）。这类文件**没有任何可提取的文字**，
   必须明确告诉用户，而不是返回空字符串 ——
   否则会切不出块、检索不到，而用户以为上传成功了。
"""

import re
import zipfile
from pathlib import Path

# 中日韩统一表意文字（含扩展 A）与常用中文标点
_CJK = (
    r"\u4e00-\u9fff"          # 基本区
    r"\u3400-\u4dbf"          # 扩展 A
    r"\uf900-\ufaff"          # 兼容表意
    r"\u3000-\u303f"          # 中文标点
    r"\uff00-\uffef"          # 全角字符
)

# 仅当空格两侧都是 CJK 时才删（见模块说明第 1 点）
_CJK_SPACE = re.compile(rf"(?<=[{_CJK}])\s+(?=[{_CJK}])")

# 中文标点前不该有空格（PDF 逐字排版常留出空档）
_CJK_BEFORE_PUNCT = re.compile(rf"\s+(?=[，。、；：？！）】》”’])")

# 句末标点后接中文：补一个换行，缓解"整页拼成一行"
_SENTENCE_BREAK = re.compile(rf"(?<=[。！？])(?=[{_CJK}])")


class UnsupportedDocumentType(ValueError):
    """文件类型不支持。"""


class NoExtractableText(ValueError):
    """文件可解析，但里面没有可提取的文字（例如纯图片型文档）。"""


class DocumentParser:
    """文档解析器。"""

    SUPPORTED_SUFFIXES = (".txt", ".md", ".pdf", ".docx")

    @staticmethod
    async def parse(file_path: str) -> str:
        """解析文档并提取纯文本。"""

        path = Path(file_path)

        if not path.exists():
            raise FileNotFoundError(f"文件不存在: {file_path}")

        suffix = path.suffix.lower()

        if suffix in (".txt", ".md"):
            text = await DocumentParser._parse_text(path)
        elif suffix == ".pdf":
            text = await DocumentParser._parse_pdf(path)
        elif suffix == ".docx":
            text = await DocumentParser._parse_docx(path)
        else:
            raise UnsupportedDocumentType(
                f"不支持的文件类型 {suffix}。"
                f"当前支持：{'、'.join(DocumentParser.SUPPORTED_SUFFIXES)}"
            )

        cleaned = DocumentParser.normalize(text)

        if not cleaned.strip():
            raise NoExtractableText(DocumentParser._empty_hint(suffix))

        return cleaned

    # ------------------------------------------------------------------
    # 各格式
    # ------------------------------------------------------------------

    @staticmethod
    async def _parse_text(path: Path) -> str:
        """TXT / Markdown。

        编码不固定：简历可能来自 Windows（GBK）。
        先试 UTF-8，失败再退 GBK，最后用替换字符兜底，
        避免一个编码问题就让上传彻底失败。
        """

        raw = path.read_bytes()

        for encoding in ("utf-8", "utf-8-sig", "gbk", "gb18030"):
            try:
                return raw.decode(encoding)
            except UnicodeDecodeError:
                continue

        return raw.decode("utf-8", errors="replace")

    @staticmethod
    async def _parse_pdf(path: Path) -> str:
        from pypdf import PdfReader

        try:
            reader = PdfReader(str(path))
        except Exception as exc:  # noqa: BLE001
            raise ValueError(f"PDF 读取失败：{exc}") from exc

        if reader.is_encrypted:
            # 有些 PDF 用空密码加密，值得试一次
            try:
                reader.decrypt("")
            except Exception as exc:  # noqa: BLE001
                raise ValueError(
                    "PDF 已加密，无法解析。请提供未加密的文件。"
                ) from exc

        pages = []

        for page in reader.pages:
            try:
                pages.append(page.extract_text() or "")
            except Exception:  # noqa: BLE001
                # 单页失败不该毁掉整份文档
                pages.append("")

        return "\n".join(pages)

    @staticmethod
    async def _parse_docx(path: Path) -> str:
        """DOCX。

        用 python-docx 取段落与表格；同时直接读 `document.xml`
        兜底 —— 文本框（`w:txbxContent`）与形状里的文字
        python-docx 取不到，而简历模板很常用这类排版。
        """

        import docx

        try:
            document = docx.Document(str(path))
        except Exception as exc:  # noqa: BLE001
            raise ValueError(f"DOCX 读取失败：{exc}") from exc

        parts: list[str] = []

        for paragraph in document.paragraphs:
            if paragraph.text.strip():
                parts.append(paragraph.text)

        for table in document.tables:
            for row in table.rows:
                cells = [
                    cell.text.strip() for cell in row.cells
                ]
                if any(cells):
                    parts.append(" | ".join(cells))

        parts.extend(DocumentParser._docx_xml_text(path))

        return "\n".join(parts)

    @staticmethod
    def _docx_xml_text(path: Path) -> list[str]:
        """从 document.xml 里按顺序取所有 `<w:t>` 文本。

        覆盖 python-docx 看不到的文本框内容。取不到就返回空。
        """

        try:
            with zipfile.ZipFile(path) as archive:
                if "word/document.xml" not in archive.namelist():
                    return []
                xml = archive.read("word/document.xml").decode(
                    "utf-8", errors="replace"
                )
        except Exception:  # noqa: BLE001
            return []

        # 先按段落切分，再取每段里的文本，保留基本的行结构
        lines = []

        for paragraph in re.findall(
            r"<w:p[ >].*?</w:p>", xml, re.S
        ):
            texts = re.findall(r"<w:t[^>]*>(.*?)</w:t>", paragraph, re.S)
            line = "".join(texts)
            if line.strip():
                lines.append(line)

        return lines

    # ------------------------------------------------------------------
    # 清洗与提示
    # ------------------------------------------------------------------

    @staticmethod
    def normalize(text: str) -> str:
        """去掉排版产生的噪音，同时**保留正常的英文词间空格**。

        - 统一换行、去掉零宽字符
        - 只删除"两侧都是汉字"的空格（PDF 逐字排版的产物）
        - 折叠多余空白行
        """

        if not text:
            return ""

        # 零宽字符与软连字符
        text = re.sub(r"[\u200b-\u200f\ufeff\u00ad]", "", text)

        text = text.replace("\r\n", "\n").replace("\r", "\n")

        # PDF 提取常见的非断行空格
        text = text.replace("\u00a0", " ")

        # 关键一步：只在汉字之间去空格
        text = _CJK_SPACE.sub("", text)

        # 中文标点前不留空格
        text = _CJK_BEFORE_PUNCT.sub("", text)

        # 行内多余空格（含全角空格）
        text = re.sub(r"[ \t\u3000]{2,}", " ", text)

        # 行尾空格
        text = re.sub(r"[ \t]+$", "", text, flags=re.M)

        # 三个以上连续空行压成两个
        text = re.sub(r"\n{3,}", "\n\n", text)

        # 最后一步：给"句末标点后紧接下一句"的位置补换行。
        #
        # 为什么需要：PDF 提取常常丢掉全部换行，一页文字变成一整行
        # （实测：一份简历 1787 字符挤在 5 行里）。那会让
        # 切块切在句子中间、也让内容难以阅读与核对。
        #
        # 只在**句末标点后**插入，且要求下一句紧跟中文 ——
        # 这样不会破坏 "FastAPI。REST" 这类中英混排的语义，
        # 也不会在已经有换行的地方重复插入。
        text = _SENTENCE_BREAK.sub("\n", text)

        return text.strip()

    @staticmethod
    def _empty_hint(suffix: str) -> str:
        """没有可提取文字时，给出**可操作**的提示。"""

        if suffix == ".pdf":
            return (
                "这个 PDF 里没有可提取的文字 —— 它很可能是"
                "扫描件或图片型 PDF（内容是一张图）。"
                "请改用「另存为文本型 PDF」或直接上传 TXT 文件。"
            )

        if suffix == ".docx":
            return (
                "这个 DOCX 里没有可提取的文字 —— 它很可能是"
                "把简历导出成了图片（整个正文就是一张图）。"
                "请改用「另存为文本型 PDF」、或把简历文字"
                "直接复制到 TXT 文件后上传。"
            )

        return (
            "文件里没有可提取的文字。"
            "请确认内容是文本而非图片。"
        )
