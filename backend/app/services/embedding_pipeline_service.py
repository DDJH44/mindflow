from sqlalchemy.ext.asyncio import AsyncSession

from app.repositories.document_chunk_repository import DocumentChunkRepository
from app.repositories.document_repository import DocumentRepository
from app.services.milvus_vector_store import MilvusVectorStore
from app.services.openai_embedding_service import OpenAIEmbeddingService


class EmbeddingPipelineService:
    """DocumentChunk Embedding 处理流程"""

    def __init__(self, db: AsyncSession):
        self.db = db
        self.chunk_repository = DocumentChunkRepository(db)
        self.document_repository = DocumentRepository(db)
        self.embedding_service = OpenAIEmbeddingService()
        self.vector_store = MilvusVectorStore()

    async def embed_document(self, document_id: int) -> int:
        """
        将指定文档的所有 Chunk 生成 Embedding。

        返回成功处理的 Chunk 数量。
        """

        document = await self.document_repository.get_by_id(document_id)

        if document is None:
            raise ValueError(f"文档不存在: {document_id}")

        chunks = await self.chunk_repository.get_pending_chunks(document_id)

        if not chunks:
            return 0

        texts = [chunk.content for chunk in chunks]

        vectors = await self.embedding_service.embed_texts(texts)

        if len(vectors) != len(chunks):
            raise RuntimeError(
                "Embedding 返回数量与 Chunk 数量不一致"
            )

        await self.vector_store.create_collection()

        await self.vector_store.insert(
            ids=[chunk.id for chunk in chunks],
            vectors=vectors,
            metadata=[
                {
                        "document_id": document.id,
                        "project_id": document.project_id,
                        "document_type": document.document_type,
                }
                for _ in chunks
            ],
        )

        for chunk in chunks:
            chunk.embedding_status = "embedded"

        await self.db.commit()

        return len(chunks)
