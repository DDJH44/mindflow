"""按产品指标测重排：每类型"取 10 → 重排 → 留 3"能否救回遗漏。

## 为什么这才是正确的测法

`measure_context_recall.py` 给出了产品视角的事实：

    答案进入上下文（每类型 top-3）：22/23 = 0.957
    答案进入候选池（每类型 top-10）：23/23 = 1.000

**检索本身几乎没问题。** 唯一没进上下文的 `project_rrf_choice`
是**排序问题**（答案在 top-10 内但没进前 3），不是召回问题。

因此重排在这里的价值**只有一个**：让那 1 例进前 3。
而那需要"扩大候选池 + 重排" —— 只取 3 个再重排没有意义
（集合不变，只是一次昂贵的换序）。

## 它也回答"上限在哪"

若重排能救回那 1 例，则产品指标从 0.957 到 **1.000**；
若救不回，则重排对产品**零收益**，不该启用。

用法：uv run python -m tests.test_rerank_context
"""

import asyncio
import statistics
import sys
import time

from app.database.session import AsyncSessionLocal

DOCUMENT_TYPES = ("resume", "jd", "project", "code")

# 候选池大小：与 `settings.rerank_candidates` 保持一致
POOL = 10

# 产品实际留给每个类型的上下文块数
KEEP = 3


async def main() -> int:
    sys.path.insert(0, r"D:\yuxi\MindFlow\backend")

    from app.evaluation.datasets import EVALUATION_CASES
    from app.evaluation.runner import EvaluationRunner
    from app.services.retrieval_service import RetrievalService

    runner = EvaluationRunner()
    await runner.seed()

    print("=" * 78)
    print("重排的产品价值：取 10 → 重排 → 留 3")
    print("=" * 78)

    rows: list[dict] = []

    try:
        async with AsyncSessionLocal() as db:
            service = RetrievalService(db)

            for case in EVALUATION_CASES:
                expected, _ = await runner.resolve_expectations(case)
                expected_set = set(expected)

                per_type: dict[str, dict] = {}

                for document_type in DOCUMENT_TYPES:
                    # ---- 现状：直接取 3 ----
                    plain = await service.retrieve(
                        query=case.query,
                        limit=KEEP,
                        project_id=runner.project_id,
                        document_type=document_type,
                        rerank=False,
                    )
                    plain_ids = [item["chunk_id"] for item in plain]

                    # ---- 候选：取 10，不重排（判断"扩大池子"本身够不够）----
                    pool = await service.retrieve(
                        query=case.query,
                        limit=POOL,
                        project_id=runner.project_id,
                        document_type=document_type,
                        rerank=False,
                    )
                    pool_ids = [item["chunk_id"] for item in pool]

                    # ---- 候选：取 10 → 重排 → 留 3 ----
                    started = time.monotonic()

                    reranked = await service.retrieve(
                        query=case.query,
                        limit=KEEP,
                        project_id=runner.project_id,
                        document_type=document_type,
                        rerank=True,
                    )

                    elapsed = (time.monotonic() - started) * 1000
                    rerank_ids = [
                        item["chunk_id"] for item in reranked
                    ]

                    per_type[document_type] = {
                        "plain": any(
                            i in expected_set for i in plain_ids
                        ),
                        "pool_only": any(
                            i in expected_set for i in pool_ids
                        ),
                        "reranked": any(
                            i in expected_set for i in rerank_ids
                        ),
                        "elapsed": elapsed,
                    }

                target = case.document_type

                def aggregate(key: str) -> bool:
                    if target is None:
                        return any(
                            per_type[t][key] for t in DOCUMENT_TYPES
                        )
                    return per_type[target][key]

                rows.append(
                    {
                        "case_id": case.id,
                        "plain": aggregate("plain"),
                        "pool_only": aggregate("pool_only"),
                        "reranked": aggregate("reranked"),
                        "elapsed": max(
                            per_type[t]["elapsed"]
                            for t in DOCUMENT_TYPES
                        ),
                    }
                )

                mark = ""

                if aggregate("reranked") and not aggregate("plain"):
                    mark = "  ← 重排救回"
                elif aggregate("plain") and not aggregate("reranked"):
                    mark = "  ← ⚠️ 重排弄丢了"

                print(
                    f"  {'✓' if aggregate('plain') else '✗'} "
                    f"{case.id[:32]:<34} "
                    f"现状={int(aggregate('plain'))} "
                    f"扩池={int(aggregate('pool_only'))} "
                    f"重排={int(aggregate('reranked'))}"
                    f"{mark}"
                )

    finally:
        await runner.cleanup()

    # ---------------- 汇总 ----------------
    print()
    print("=" * 78)
    print("产品指标（答案是否进入该类型的上下文）")
    print("=" * 78)

    n = len(rows)

    for label, key in (
        ("现状：直接取 3", "plain"),
        ("只扩大候选池、不重排", "pool_only"),
        ("取 10 → 重排 → 留 3", "reranked"),
    ):
        hit = sum(1 for row in rows if row[key])
        print(f"  {label:<24} {hit}/{n} = {hit / n:.3f}")

    print()
    print("  逐例变化：")
    recovered = [
        row["case_id"]
        for row in rows
        if row["reranked"] and not row["plain"]
    ]
    lost = [
        row["case_id"]
        for row in rows
        if row["plain"] and not row["reranked"]
    ]

    print(f"    救回 {len(recovered)} 例：{recovered}")
    print(f"    弄丢 {len(lost)} 例：{lost}")

    # ---------------- 代价 ----------------
    print()
    print("=" * 78)
    print("代价")
    print("=" * 78)

    per_type_ms = statistics.median(
        row["elapsed"] for row in rows
    )

    print(
        f"  单次「取 10 → 重排 → 留 3」的耗时（含 embedding）："
        f"中位 {per_type_ms:.0f}ms"
    )
    print()
    print("  每次出题调 4 个类型，因此额外等待：")
    print(f"    串行 4 次：约 {per_type_ms * 4 / 1000:.1f} 秒")
    print(
        f"    并发 4 次：约 {per_type_ms / 1000:.1f} 秒"
        f"（互不依赖，可实现并发）"
    )

    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
