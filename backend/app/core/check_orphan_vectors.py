"""检查并清理 Milvus 中的孤儿向量（D52）。

孤儿向量 = Milvus 里有、但数据库 `document_chunks` 里已不存在的向量。
它的危害是**静默**的：`RetrievalService.retrieve` 命中后回表取不到正文、
只是 `continue` 跳过 —— 但**已经占用了 top-k 名额**，
于是实际召回数变少，表现为"检索变差了"。

成因（D52）：修复前删除文档只删数据库记录，
Milvus 里的向量不会被连带删除。

用法：

    # 只检查
    uv run python -m app.core.check_orphan_vectors

    # 检查并清理
    uv run python -m app.core.check_orphan_vectors --purge
"""

import argparse
import asyncio
import sys

from sqlalchemy import text

from app.database.session import AsyncSessionLocal
from app.services.milvus_vector_store import MilvusVectorStore
from app.services.openai_embedding_service import (
    OpenAIEmbeddingService,
)

# 扫描范围。取足够大以覆盖开发库的向量量级。
SCAN_LIMIT = 500

# 用于召回扫描的查询。
# 刻意写成一串领域词而不是自然句：目的是**广度**而非排序质量，
# 我们只关心"Milvus 里存在哪些 id"。
SCAN_QUERY = (
    "简历 项目经历 chunk 切分 overlap 向量检索 面试问题 "
    "PostgreSQL Milvus Redis FastAPI 缓存 索引"
)


async def collect_orphans(store: MilvusVectorStore) -> tuple[set[int], set[int]]:
    """返回 (孤儿的 chunk id, 扫描到的全部 id)。

    扫描**不带 project_id 过滤**：孤儿可能属于任何项目，
    漏扫会让污染长期残留。
    """

    async with AsyncSessionLocal() as db:
        live_ids = set(
            (
                await db.execute(
                    text("SELECT id FROM document_chunks")
                )
            ).scalars().all()
        )

    embedding = OpenAIEmbeddingService()
    vectors = await embedding.embed_texts([SCAN_QUERY])

    hits = await store.search(
        vector=vectors[0],
        limit=SCAN_LIMIT,
    )

    milvus_ids = {int(item["id"]) for item in hits}

    return milvus_ids - live_ids, milvus_ids


async def main() -> int:
    parser = argparse.ArgumentParser(
        description="检查（并可清理）Milvus 中的孤儿向量",
    )
    parser.add_argument(
        "--purge",
        action="store_true",
        help="删除检测到的孤儿向量",
    )
    args = parser.parse_args()

    store = MilvusVectorStore()

    async with AsyncSessionLocal() as db:
        live_count = (
            await db.execute(
                text("SELECT COUNT(*) FROM document_chunks")
            )
        ).scalar_one()

    print("=" * 74)
    print("Milvus 孤儿向量检查")
    print("=" * 74)
    print(f"  数据库现存 chunk 数: {live_count}")

    orphans, scanned = await collect_orphans(store)

    print(f"  扫描返回向量数: {len(scanned)}（上限 {SCAN_LIMIT}）")
    print(f"  孤儿向量数: {len(orphans)}")

    if not orphans:
        print()
        print("  ✓ 未发现孤儿向量")
        return 0

    print(f"  孤儿 id: {sorted(orphans)}")

    if not args.purge:
        print()
        print("  ✗ 存在孤儿向量 —— 它们占用 top-k 名额、静默降低召回")
        print("    加 --purge 可清理")
        return 1

    print()
    print("  正在清理…")
    await store.delete(ids=sorted(orphans))

    remaining, _ = await collect_orphans(store)

    print(f"  清理后剩余孤儿: {len(remaining)}")

    if remaining:
        print(f"  ✗ 仍有残留: {sorted(remaining)}")
        return 1

    print("  ✓ 清理完成")
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
