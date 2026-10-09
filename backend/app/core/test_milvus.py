import asyncio

from app.services.milvus_vector_store import MilvusVectorStore
from app.services.openai_embedding_service import OpenAIEmbeddingService


async def main():
    milvus = MilvusVectorStore()
    embedding = OpenAIEmbeddingService()

    # 确保 Collection 存在
    await milvus.create_collection()

    # 测试文本
    text = """
    MindFlow 是一个 AI 面试助手系统。
    用户可以上传简历、岗位 JD 和项目文档。
    系统通过 RAG 检索用户的项目经历，
    生成个性化的面试问题和追问。
    """

    # 1. Qwen 生成 Embedding
    vector = await embedding.embed_text(text)

    print("Embedding 维度:", len(vector))

    # 2. 写入 Milvus
    chunk_id = 1000000

    await milvus.insert(
        ids=[chunk_id],
        vectors=[vector],
        metadata=[
            {
                "document_id": 6,
                "project_id": 1,
            }
        ],
    )

    print("向量写入 Milvus 成功")

    # 3. 从 Milvus 搜索
    results = await milvus.search(
        vector=vector,
        limit=3,
    )

    print("Milvus 搜索结果:")
    print(results)


if __name__ == "__main__":
    asyncio.run(main())