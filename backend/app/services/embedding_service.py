from abc import ABC, abstractmethod


class EmbeddingService(ABC):
    """Embedding 服务抽象接口"""

    @abstractmethod
    async def embed_text(self, text: str) -> list[float]:
        """将单段文本转换为向量"""
        raise NotImplementedError

    @abstractmethod
    async def embed_texts(
        self,
        texts: list[str],
    ) -> list[list[float]]:
        """批量将文本转换为向量"""
        raise NotImplementedError