import asyncio

from app.database.session import AsyncSessionLocal
from app.services.retrieval_service import RetrievalService


async def main():
    async with AsyncSessionLocal() as db:
        service = RetrievalService(db)

        results = await service.retrieve(
            query="我的技术项目经历",
            project_id=3,
            document_type="resume",
            limit=5,
        )

        print("\n===== Retrieval Result =====")

        for item in results:
            print(
                {
                    "chunk_id": item["chunk_id"],
                    "document_id": item["document_id"],
                    "project_id": item["project_id"],
                    "document_type": item["document_type"],
                    "score": item["score"],
                    "content": item["content"][:100],
                }
            )


if __name__ == "__main__":
    asyncio.run(main())