from typing import List


class ChunkingService:
    """文档文本切块服务"""

    DEFAULT_CHUNK_SIZE = 500
    DEFAULT_CHUNK_OVERLAP = 100

    @staticmethod
    def split_text(
        text: str,
        chunk_size: int = DEFAULT_CHUNK_SIZE,
        chunk_overlap: int = DEFAULT_CHUNK_OVERLAP,
    ) -> List[str]:
        """
        将长文本切分成多个文本块。

        Args:
            text: 原始文本
            chunk_size: 每个文本块最大字符数
            chunk_overlap: 相邻文本块重叠字符数

        Returns:
            文本块列表
        """

        if not text or not text.strip():
            return []

        if chunk_size <= 0:
            raise ValueError("chunk_size 必须大于 0")

        if chunk_overlap < 0:
            raise ValueError("chunk_overlap 不能小于 0")

        if chunk_overlap >= chunk_size:
            raise ValueError(
                "chunk_overlap 必须小于 chunk_size"
            )

        text = text.strip()

        chunks = []

        start = 0
        text_length = len(text)

        while start < text_length:
            end = min(
                start + chunk_size,
                text_length,
            )

            chunk = text[start:end].strip()

            if chunk:
                chunks.append(chunk)

            if end >= text_length:
                break

            start = end - chunk_overlap

        return chunks