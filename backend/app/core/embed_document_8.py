import asyncio

from app.database.session import AsyncSessionLocal
from app.services.embedding_pipeline_service import EmbeddingPipelineService


async def main():
    async with AsyncSessionLocal() as db:
        service = EmbeddingPipelineService(db)

        count = await service.embed_document(8)

        print(f"Embedding 完成，处理 Chunk 数量: {count}")


if __name__ == "__main__":
    asyncio.run(main())