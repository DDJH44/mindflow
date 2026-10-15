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
        """批量写入文本块。返回写入的对象（`id` 已回填）。

        ## ⚠️ 这里曾经有一个 N+1（已修，实测数据见下）

        原实现在 `commit()` 之后对**每个块**调用一次 `refresh()`：

        ```python
        await self.session.commit()
        for chunk in document_chunks:
            await self.session.refresh(chunk)   # ← 每个块一次 SELECT
        ```

        实测查询次数（`measure_chunk_insert_queries.py`）：

        | 块数 | 总查询 | 构成 |
        | --- | --- | --- |
        | 10 | 11 | SELECT=10 + INSERT=1 |
        | 100 | 101 | SELECT=100 + INSERT=1 |
        | 500 | **501** | SELECT=500 + INSERT=1 |

        大文件实测约 1800 块 → 每次上传**多约 1800 次往返**。

        ## 为什么可以安全去掉

        那 N 次 `refresh` 是**完全多余的**，理由有三：

        1. **没有服务端生成的列需要回读。**
           `DocumentChunk` 的 `embedding_status` 与 `created_at`
           都是 **Python 侧默认值**（`default=`），在 flush 时就已填好；
           表里没有任何 `server_default` / `onupdate` 列。
        2. **`id` 在 INSERT 时就拿回来了。**
           PostgreSQL 方言的 INSERT 带 `RETURNING id`，
           SQLAlchemy 据此回填主键 —— 实测 100 块全部有 id，
           且**读取 id 不产生任何查询**。
        3. **`expire_on_commit=False`**（本项目已设），
           因此 commit 不会把对象标记为过期，属性仍然可读。

        ## 验证方式

        `test_chunk_insert_efficiency.py` 断言：
        - 100 块的查询次数 < 100（不是线性增长）
        - 每个块的 `id` / `created_at` 都已回填
        - **读取这些属性不触发任何查询**（异步 ORM 的经典坑：
          过期属性一旦被访问会发起 IO，在同步上下文抛 MissingGreenlet）
        """

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