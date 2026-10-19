import asyncio

from sqlalchemy import select

from app.database.session import AsyncSessionLocal
from app.models.document import Document
from app.models.document_chunk import DocumentChunk


async def main():
    async with AsyncSessionLocal() as db:
        # 找到现有 Document 6
        result = await db.execute(
            select(Document).where(Document.id == 6)
        )

        document = result.scalar_one_or_none()

        if not document:
            raise RuntimeError("Document 6 不存在")

        # 找当前最大的 chunk_index
        result = await db.execute(
            select(DocumentChunk)
            .where(DocumentChunk.document_id == document.id)
            .order_by(DocumentChunk.chunk_index.desc())
            .limit(1)
        )

        last_chunk = result.scalar_one_or_none()

        next_index = (
            last_chunk.chunk_index + 1
            if last_chunk
            else 0
        )

        chunk = DocumentChunk(
            document_id=document.id,
            content=(
                "这是 MindFlow Embedding Pipeline 的测试 Chunk。"
                "用于验证文本经过 Qwen Embedding 后，"
                "能够通过 Milvus upsert 写入向量数据库，"
                "并且最终可以通过向量搜索重新检索到该 Chunk。"
            ),
            chunk_index=next_index,
            chunk_metadata={
                "source": "embedding_pipeline_test"
            },
            embedding_status="pending",
        )

        db.add(chunk)
        await db.commit()
        await db.refresh(chunk)

        print("=" * 60)
        print("测试 Chunk 创建成功")
        print("Chunk ID:", chunk.id)
        print("Document ID:", chunk.document_id)
        print("Chunk Index:", chunk.chunk_index)
        print("Embedding Status:", chunk.embedding_status)
        print("=" * 60)


if __name__ == "__main__":
    asyncio.run(main())