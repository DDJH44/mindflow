from openai import AsyncOpenAI

from app.core.config import settings
from app.services.embedding_service import EmbeddingService


class OpenAIEmbeddingService(EmbeddingService):
    """基于 OpenAI-compatible API 的 Embedding 实现"""

    def __init__(self):
        self.client = AsyncOpenAI(
            api_key=settings.embedding_api_key,
            base_url=settings.embedding_base_url or None,
        )

        self.model = settings.embedding_model

    async def embed_text(
        self,
        text: str,
    ) -> list[float]:
        """将单段文本转换为向量"""

        if not text.strip():
            raise ValueError("Embedding 文本不能为空")

        response = await self.client.embeddings.create(
            model=self.model,
            input=text,
        )

        return response.data[0].embedding

    async def embed_texts(
        self,
        texts: list[str],
    ) -> list[list[float]]:
        """批量将文本转换为向量"""

        if not texts:
            return []

        response = await self.client.embeddings.create(
            model=self.model,
            input=texts,
        )

        return [
            item.embedding
            for item in response.data
        ]