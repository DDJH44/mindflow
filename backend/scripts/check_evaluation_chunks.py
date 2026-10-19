import asyncio

from sqlalchemy import select

from app.database.session import AsyncSessionLocal
from app.models.document import Document
from app.models.document_chunk import DocumentChunk


async def main():
    async with AsyncSessionLocal() as db:
        document_result = await db.execute(
            select(Document)
            .where(Document.id.in_([6, 7, 8]))
            .order_by(Document.id)
        )

        documents = document_result.scalars().all()

        print("\n========== Evaluation Documents ==========\n")

        for document in documents:
            print(
                f"Document "
                f"id={document.id} | "
                f"project_id={document.project_id} | "
                f"type={document.document_type} | "
                f"name={document.name} | "
                f"status={document.status}"
            )

            chunk_result = await db.execute(
                select(DocumentChunk)
                .where(
                    DocumentChunk.document_id == document.id
                )
                .order_by(DocumentChunk.chunk_index)
            )

            chunks = chunk_result.scalars().all()

            if not chunks:
                print("  没有 Chunk")
                continue

            for chunk in chunks:
                print(
                    f"  Chunk "
                    f"id={chunk.id} | "
                    f"index={chunk.chunk_index} | "
                    f"embedding_status={chunk.embedding_status}"
                )

                print(
                    f"    content={chunk.content[:120]!r}"
                )

            print()


if __name__ == "__main__":
    asyncio.run(main())