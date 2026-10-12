from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
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
        rerank: bool | None = None,
    ) -> list[dict]:
        """检索。`rerank=None` 时跟随 `settings.rerank_enabled`。

        显式传 `rerank=False` 让评估可以在同一进程里
        对比"开与不开"的结果 —— 那是判断它值不值得的唯一办法。
        """

        if not query.strip():
            raise ValueError("检索问题不能为空")

        use_rerank = (
            settings.rerank_enabled if rerank is None else rerank
        )

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

        # 需要重排时先取更多候选。
        #
        # 取与 `limit` 相同的数量等于**只换顺序、不换集合** ——
        # 重排的最大价值恰恰来自更大的候选池
        # （基线显示正确答案常在 top-5 内但排在第 2–5 位）。
        search_limit = (
            max(limit, settings.rerank_candidates)
            if use_rerank
            else limit
        )

        results = await self.vector_store.search(
            vector=query_vector,
            limit=search_limit,
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

        if not use_rerank:
            return retrieved_chunks[:limit]

        from app.services.rerank_service import RerankService

        return await RerankService().rerank(
            query=query,
            candidates=retrieved_chunks,
            top_k=limit,
        )