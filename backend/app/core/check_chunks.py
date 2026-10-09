import asyncio

from sqlalchemy import select

from app.database.session import AsyncSessionLocal
from app.models.document_chunk import DocumentChunk


async def main():
    async with AsyncSessionLocal() as db:
        result = await db.execute(
            select(DocumentChunk)
            .order_by(DocumentChunk.id.asc())
        )

        chunks = result.scalars().all()

        print("=" * 60)
        print("当前 Chunk 状态")
        print("=" * 60)

        if not chunks:
            print("数据库中没有 Chunk")
            return

        for chunk in chunks:
            print(
                f"Chunk ID={chunk.id} | "
                f"Document ID={chunk.document_id} | "
                f"Index={chunk.chunk_index} | "
                f"Status={chunk.embedding_status}"
            )

        print("=" * 60)


if __name__ == "__main__":
    asyncio.run(main())