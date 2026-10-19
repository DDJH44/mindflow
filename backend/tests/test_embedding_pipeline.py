import asyncio

from sqlalchemy import select

from app.database.session import AsyncSessionLocal
from app.models.document_chunk import DocumentChunk
from app.models.document import Document
from app.services.embedding_pipeline_service import EmbeddingPipelineService
from app.services.milvus_vector_store import MilvusVectorStore
from app.services.openai_embedding_service import OpenAIEmbeddingService


async def main():
    async with AsyncSessionLocal() as db:
        # 1. 自动寻找一个 pending Chunk
        result = await db.execute(
            select(DocumentChunk, Document)
            .join(Document, Document.id == DocumentChunk.document_id)
            .where(DocumentChunk.embedding_status == "pending")
            .order_by(DocumentChunk.id.asc())
            .limit(1)
        )

        row = result.first()

        if not row:
            print("没有找到 pending Chunk")
            print("请先上传一个新文档，或准备一个 embedding_status=pending 的 Chunk")
            return

        chunk, document = row

        print("=" * 60)
        print("找到 Pending Chunk")
        print("Chunk ID:", chunk.id)
        print("Document ID:", chunk.document_id)
        print("Project ID:", document.project_id)
        print("原状态:", chunk.embedding_status)
        print("=" * 60)

        # 2. 执行完整 Embedding Pipeline
        service = EmbeddingPipelineService(db)

        count = await service.embed_document(document_id=document.id)

        print()
        print("Embedding Pipeline 处理数量:", count)

        # 3. 重新查询数据库状态
        result = await db.execute(
            select(DocumentChunk).where(
                DocumentChunk.id == chunk.id
            )
        )

        updated_chunk = result.scalar_one()

        print("数据库状态:", updated_chunk.embedding_status)

        if updated_chunk.embedding_status != "embedded":
            raise RuntimeError(
                f"Chunk {chunk.id} 状态异常："
                f"{updated_chunk.embedding_status}"
            )

        # 4. 使用同样的文本重新生成 Query Embedding
        embedding_service = OpenAIEmbeddingService()

        query_vector = await embedding_service.embed_text(
            updated_chunk.content
        )

        print("Query Embedding 维度:", len(query_vector))

        # 5. 查询 Milvus
        milvus = MilvusVectorStore()

        results = await milvus.search(
            vector=query_vector,
            limit=5,
        )

        print()
        print("Milvus 搜索结果:")

        found = False

        for item in results:
            print(item)

            if item.get("id") == updated_chunk.id:
                found = True

        print()

        if not found:
            raise RuntimeError(
                f"Milvus 中没有检索到 Chunk {updated_chunk.id}"
            )

        print("=" * 60)
        print("Embedding Pipeline 完整链路验证成功")
        print("=" * 60)


if __name__ == "__main__":
    asyncio.run(main())