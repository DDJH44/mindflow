from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.document_chunk import DocumentChunk
from app.services.milvus_vector_store import MilvusVectorStore
from app.services.openai_embedding_service import OpenAIEmbeddingService


class RetrievalService:
    def __init__(self, db: AsyncSession):
        self.db = db
        self.embedding_service = OpenAIEmbeddingService()
        self.vector_store = MilvusVectorStore()

    async def retrieve(
        self,
        query: str,
        limit: int = 5,
        project_id: int | None = None,
        document_type: str | None = None,
    ) -> list[dict]:
        if not query.strip():
            raise ValueError("检索问题不能为空")

        query_vector = await self.embedding_service.embed_text(query)

        filters = []

        if project_id is not None:
            filters.append(
                f"project_id == {project_id}"
            )

        if document_type is not None:
            filters.append(
                f'document_type == "{document_type}"'
            )

        filter_expression = (
            " and ".join(filters)
            if filters
            else None
        )

        results = await self.vector_store.search(
            vector=query_vector,
            limit=limit,
            filters=filter_expression,
        )

        if not results:
            return []

        chunk_ids = [
            item["id"]
            for item in results
        ]

        db_result = await self.db.execute(
            select(DocumentChunk).where(
                DocumentChunk.id.in_(chunk_ids)
            )
        )

        chunks = {
            chunk.id: chunk
            for chunk in db_result.scalars().all()
        }

        retrieved_chunks = []

        for item in results:
            chunk_id = item["id"]
            chunk = chunks.get(chunk_id)

            if not chunk:
                continue

            entity = item["entity"]

            retrieved_chunks.append(
                {
                    "chunk_id": chunk.id,
                    "document_id": chunk.document_id,
                    "project_id": entity["project_id"],
                    "document_type": entity["document_type"],
                    "content": chunk.content,
                    "score": item["distance"],
                }
            )

        return retrieved_chunks