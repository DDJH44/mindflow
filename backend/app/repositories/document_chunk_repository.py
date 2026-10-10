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
    
    async def get_by_ids(
        self,
        chunk_ids: list[int],
    ) -> dict[int, DocumentChunk]:
        """按 id 批量取 chunk，返回 {id: chunk}。

        返回字典而不是列表：调用方（资料依据）要按原顺序重排，
        而 `IN (...)` 不保证顺序。顺序很重要 ——
        证据的次序反映了它在喂给模型时的位置（§9.3）。
        """

        if not chunk_ids:
            return {}

        result = await self.session.execute(
            select(DocumentChunk).where(
                DocumentChunk.id.in_(chunk_ids)
            )
        )

        return {
            chunk.id: chunk for chunk in result.scalars().all()
        }

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