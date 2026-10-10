"""检查并修复向量索引的两类状态不一致。

两类问题方向相反，但危害相同 —— 都让用户看不出真实情况：

| 类型 | 表现 | 后果 |
| --- | --- | --- |
| **A. chunk 说 embedded，但 Milvus 里没有** | 检索永远命中不到 | 界面显示"已索引"却搜不到 |
| **B. 文档说 chunked，但 chunk 全部已嵌入** | 资料页显示"已切块·未索引" | 顶部提示"还没有可被检索的资料"，用户反复点"重试索引" |

成因：早期代码路径中途失败但已提交状态；或手工修复时只改了 chunk
没改文档级状态。

用法：

    # 只检查
    uv run python -m app.core.check_embedded_consistency

    # 检查并修复
    uv run python -m app.core.check_embedded_consistency --repair
"""

import argparse
import asyncio
import sys

from sqlalchemy import text

from app.database.session import AsyncSessionLocal
from app.services.milvus_vector_store import MilvusVectorStore


async def find_phantom_chunks() -> tuple[dict[int, int], list[int]]:
    """类型 A：声称已嵌入、但 Milvus 里不存在的 chunk。

    返回 (claimed 映射, 缺失的 chunk id 列表)。

    **为什么必须用 `list_all_ids()` 而不是 `search`**：
    类型 A 的判据是"这个 chunk 在 Milvus 里**不存在**"，
    而 `search` 是 top-K 相似度查询 —— 它只返回离查询向量最近的
    K 条。用它来判断"不存在"会产生**假阳性**：
    真实存在、只是没落进这 K 条的 chunk 会被误报为幻影，
    于是脚本会去"修复"一个没坏的东西。

    此前这里确实是 `store.search(..., limit=500)`。
    """

    async with AsyncSessionLocal() as db:
        rows = list(
            (
                await db.execute(
                    text(
                        "SELECT id, document_id "
                        "FROM document_chunks "
                        "WHERE embedding_status = 'embedded' "
                        "ORDER BY id"
                    )
                )
            ).all()
        )

    claimed = {row[0]: row[1] for row in rows}

    if not claimed:
        return {}, []

    present = await MilvusVectorStore().list_all_ids()

    return claimed, sorted(set(claimed) - present)


async def find_stale_documents() -> list[tuple[int, str, str]]:
    """类型 B：文档状态与其 chunk 的真实情况不符。

    返回 [(document_id, 当前状态, 应为的状态)]。
    """

    async with AsyncSessionLocal() as db:
        rows = list(
            (
                await db.execute(
                    text(
                        """
                        SELECT d.id, d.status,
                               COUNT(c.id) AS total,
                               SUM(
                                   CASE
                                     WHEN c.embedding_status = 'embedded'
                                     THEN 1 ELSE 0
                                   END
                               ) AS embedded
                        FROM documents d
                        LEFT JOIN document_chunks c
                               ON c.document_id = d.id
                        GROUP BY d.id, d.status
                        ORDER BY d.id
                        """
                    )
                )
            ).all()
        )

    stale = []

    for document_id, status, total, embedded in rows:
        total = total or 0
        embedded = embedded or 0

        if not total:
            # 没有 chunk 的文档不参与对齐：它的状态由解析流程决定
            continue

        expected = "embedded" if embedded == total else "chunked"

        if status != expected:
            stale.append((document_id, status, expected))

    return stale


async def main() -> int:
    parser = argparse.ArgumentParser(
        description="检查（并修复）向量索引的状态不一致",
    )
    parser.add_argument(
        "--repair",
        action="store_true",
        help="修复检测到的不一致",
    )
    args = parser.parse_args()

    print("=" * 74)
    print("向量索引状态一致性检查")
    print("=" * 74)

    # ---------------- 类型 A ----------------
    claimed, missing = await find_phantom_chunks()

    print()
    print("[类型 A] chunk 声称已嵌入，但 Milvus 中不存在")
    print(f"  声称已嵌入的 chunk 数: {len(claimed)}")
    print(f"  向量缺失的 chunk 数: {len(missing)}")
    for chunk_id in missing:
        print(
            f"    chunk {chunk_id}（文档 {claimed[chunk_id]}）向量缺失"
        )

    # ---------------- 类型 B ----------------
    stale = await find_stale_documents()

    print()
    print("[类型 B] 文档状态与 chunk 真实情况不符")
    print(f"  状态不符的文档数: {len(stale)}")
    for document_id, current, expected in stale:
        print(
            f"    文档 {document_id}: {current} → {expected}"
        )

    if not missing and not stale:
        print()
        print("  ✓ 两类均一致")
        return 0

    if not args.repair:
        print()
        print("  加 --repair 可修复")
        return 1

    # ---------------- 修复 ----------------
    print()
    print("正在修复…")

    if missing:
        async with AsyncSessionLocal() as db:
            await db.execute(
                text(
                    "UPDATE document_chunks "
                    "SET embedding_status = 'pending' "
                    "WHERE id = ANY(:ids)"
                ),
                {"ids": missing},
            )
            await db.commit()

        affected = sorted({claimed[c] for c in missing})
        print(f"  A: 已把 {len(missing)} 个 chunk 重置为 pending")
        print(
            f"     受影响文档 {affected} —— 用 POST "
            "/api/projects/{pid}/documents/{did}/embed 补嵌"
        )

    if stale:
        async with AsyncSessionLocal() as db:
            for document_id, _current, expected in stale:
                await db.execute(
                    text(
                        "UPDATE documents SET status = :s "
                        "WHERE id = :d"
                    ),
                    {"s": expected, "d": document_id},
                )
            await db.commit()

        print(f"  B: 已对齐 {len(stale)} 个文档的状态")

    # ---------------- 复检 ----------------
    print()
    print("复检…")
    _, missing_after = await find_phantom_chunks()
    stale_after = await find_stale_documents()

    print(f"  A 剩余: {len(missing_after)}")
    print(f"  B 剩余: {len(stale_after)}")

    if missing_after or stale_after:
        print()
        print("  ✗ 仍有残留")
        return 1

    print()
    print("  ✓ 修复完成")

    if missing:
        print(
            "  注意：A 类被重置为 pending 的 chunk 需要重新嵌入"
            "（调用 embed 端点），否则它们的文档会停在 chunked。"
        )

    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
