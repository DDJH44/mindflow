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

# 扫描范围。
#
# ⚠️ 此前这里有一个 `SCAN_LIMIT = 500`，并且用一次 top-K **相似度
# 搜索**来"枚举"库里的 id。那是错的：相似度搜索只返回离查询向量
# 最近的 K 条，于是
#
# - 向量数超过 K 时每次只看到 K 条，而且**每次看到的都不同**
#   （实测表现为"每清理一次只删掉 1–2 个"）
# - 检测本身不可靠：没落进这 K 条的向量永远查不出来
#
# 现在改用 `MilvusVectorStore.list_all_ids()`（`query` + 过滤表达式），
# 这才能真正枚举。因此不再需要"扫描查询"与"扫描上限"。


async def collect_orphans(
    store: MilvusVectorStore,
) -> tuple[set[int], set[int]]:
    """返回 (孤儿的 chunk id, Milvus 中的全部 id)。

    孤儿可能属于任何项目，因此数据库侧也不带 project 过滤 ——
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

    milvus_ids = await store.list_all_ids()

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

    print(f"  Milvus 中的向量数: {len(scanned)}")
    print(f"  孤儿向量数: {len(orphans)}")

    if not orphans:
        print()
        print("  ✓ 未发现孤儿向量")
        return 0

    # 只打印前 20 个：孤儿可能有几百个，
    # 全打出来会把真正的结论淹没。
    listed = sorted(orphans)
    preview = listed[:20]
    more = len(listed) - len(preview)

    print(f"  孤儿 id: {preview}" + (f" …还有 {more} 个" if more else ""))

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
