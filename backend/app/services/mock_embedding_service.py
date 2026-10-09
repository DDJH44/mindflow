from app.services.embedding_service import EmbeddingService


class MockEmbeddingService(EmbeddingService):
    """用于开发阶段测试的 Embedding 实现"""

    DIMENSION = 8

    async def embed_text(
        self,
        text: str,
    ) -> list[float]:
        return [0.0] * self.DIMENSION

    async def embed_texts(
        self,
        texts: list[str],
    ) -> list[list[float]]:
        return [
            await self.embed_text(text)
            for text in texts
        ]