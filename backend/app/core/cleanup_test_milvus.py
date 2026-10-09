import asyncio

from app.services.milvus_vector_store import MilvusVectorStore


TEST_IDS = [999999, 1000000]


async def main():
    milvus = MilvusVectorStore()

    print("=" * 60)
    print("准备删除 Milvus 测试数据")
    print("测试 ID:", TEST_IDS)
    print("=" * 60)

    await milvus.delete(TEST_IDS)

    print()
    print("测试向量删除成功")
    print("=" * 60)


if __name__ == "__main__":
    asyncio.run(main())