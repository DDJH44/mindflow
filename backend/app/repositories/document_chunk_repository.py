from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.document_chunk import DocumentChunk


class DocumentChunkRepository:
    """文档文本块数据访问层"""

    def __init__(self, session: AsyncSession):
        self.session = session

    async def get_by_document(
        self,
        document_id: int,
    ) -> list[DocumentChunk]:
        result = await self.session.execute(
            select(DocumentChunk)
            .where(
                DocumentChunk.document_id == document_id
            )
            .order_by(
                DocumentChunk.chunk_index.asc()
            )
        )

        return list(result.scalars().all())
    
    async def get_pending_chunks(
        self,
        document_id: int,
    ) -> list[DocumentChunk]:
        result = await self.session.execute(
            select(DocumentChunk)
            .where(
                DocumentChunk.document_id == document_id,
                DocumentChunk.embedding_status == "pending",
            )
            .order_by(DocumentChunk.chunk_index.asc())
        )
        return list(result.scalars().all())

    async def create_chunks(
        self,
        document_id: int,
        chunks: list[str],
        chunk_metadata: dict | None = None,
    ) -> list[DocumentChunk]:
        document_chunks = [
            DocumentChunk(
                document_id=document_id,
                content=content,
                chunk_index=index,
                chunk_metadata=chunk_metadata.copy() if chunk_metadata else None,
            )
            for index, content in enumerate(chunks)
        ]

        self.session.add_all(document_chunks)

        await self.session.commit()

        for chunk in document_chunks:
            await self.session.refresh(chunk)

        return document_chunks

    async def delete_by_document(
        self,
        document_id: int,
    ) -> None:
        result = await self.session.execute(
            select(DocumentChunk)
            .where(
                DocumentChunk.document_id == document_id
            )
        )

        chunks = result.scalars().all()

        for chunk in chunks:
            await self.session.delete(chunk)

        await self.session.commit()