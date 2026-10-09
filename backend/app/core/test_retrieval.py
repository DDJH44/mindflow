import asyncio

from app.database.session import AsyncSessionLocal
from app.services.retrieval_service import RetrievalService


async def main():
    async with AsyncSessionLocal() as db:
        service = RetrievalService(db)

        query = "MindFlow AI 面试助手是如何使用 RAG 检索项目经历的？"

        print("=" * 60)
        print("开始 RAG Retrieval 测试")
        print("Query:", query)
        print("=" * 60)

        results = await service.retrieve(
        query=query,
        limit=5,
        project_id=3,
    )

        print()
        print("检索结果数量:", len(results))

        for index, item in enumerate(results, start=1):
            print()
            print(f"--- Result {index} ---")
            print("Chunk ID:", item["chunk_id"])
            print("Document ID:", item["document_id"])
            print("Project ID:", item["project_id"])
            print("Score:", item["score"])
            print("Content:")
            print(item["content"])

        print()
        print("=" * 60)

        if results:
            print("RAG Retrieval 测试成功")
        else:
            print("没有检索到结果")

        print("=" * 60)


if __name__ == "__main__":
    asyncio.run(main())