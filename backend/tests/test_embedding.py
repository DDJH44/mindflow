import asyncio

from app.services.openai_embedding_service import (
    OpenAIEmbeddingService,
)


async def main():

    service = OpenAIEmbeddingService()

    text = """
    MindFlow 是一个 AI 面试助手系统。
    用户可以上传简历和项目文档，
    系统通过 RAG 技术生成个性化面试问题。
    """

    vector = await service.embed_text(
        text
    )

    print(
        "向量维度:",
        len(vector),
    )

    print(
        "前10个向量值:",
        vector[:10],
    )


if __name__ == "__main__":
    asyncio.run(main())