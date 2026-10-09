import asyncio

from sqlalchemy import select

from app.database.session import AsyncSessionLocal
from app.models.document_chunk import DocumentChunk


async def main():
    async with AsyncSessionLocal() as db:
        result = await db.execute(
            select(DocumentChunk)
            .where(DocumentChunk.document_id == 8)
            .order_by(DocumentChunk.chunk_index.asc())
        )

        chunks = list(result.scalars().all())

        print(f"Chunk 数量: {len(chunks)}")

        for chunk in chunks:
            print(f"\nChunk ID: {chunk.id}")
            print(f"Chunk Index: {chunk.chunk_index}")
            print(f"Metadata: {chunk.chunk_metadata}")
            print(f"Content: {chunk.content[:100]}")


if __name__ == "__main__":
    asyncio.run(main())