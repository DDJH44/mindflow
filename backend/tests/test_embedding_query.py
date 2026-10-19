import asyncio

from app.services.openai_embedding_service import OpenAIEmbeddingService


async def main():
    service = OpenAIEmbeddingService()

    queries = [
        "我使用了哪些后端技术？",
        "我参与开发过什么项目？",
        "这个项目使用了什么技术来实现知识检索？",
    ]

    for index, query in enumerate(queries, start=1):
        print(f"\n========== Query {index} ==========")
        print(f"文本: {query}")

        try:
            vector = await service.embed_text(query)

            print("Embedding 成功")
            print(f"维度: {len(vector)}")
            print(f"前5个值: {vector[:5]}")

        except Exception as exc:
            print("Embedding 失败")
            print(f"类型: {type(exc).__name__}")
            print(f"错误: {exc}")


if __name__ == "__main__":
    asyncio.run(main())