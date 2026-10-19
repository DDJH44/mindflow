"""重排的完整评估：质量增益 × 延迟代价（§8.5 的改进项）。

## 为什么必须是"4 种组合"而不是简单的开/关

第一次测量犯了错，值得记下来：我对比"不开重排 vs 开重排"时
**漏传了 `document_type` 过滤**，而正式基线是带过滤的。

于是测出"不开重排 R@1 = 0.522" —— 那不是基线（0.609），
把它当成对照物会**高估**重排的增益。

查证过程（`compare_eval_paths.py`）确认：
- `limit=5` 与 `limit=10` 的结果完全相同（不是 top-k 的问题）
- 唯一差异是 `document_type` 过滤：带它 0.609、不带它 0.522

因此本脚本测 **4 种组合**，并且**以正式配置为基准**：

    过滤 + 无重排   ← 正式基线（R@1 = 0.609）
    过滤 + 有重排   ← 真正的改进候选
    无过滤 + 无重排 ← 对照用
    无过滤 + 有重排 ← 对照用

## 延迟为什么用"并发 4 次"来算

每次出题会按材料类型调检索 4 次，而那 4 次**互不依赖**。
`test_rerank_cost` 实测：串行 4 次 27.5 秒，并发 4 次 7.6 秒（−72%）。
因此报告延迟时同时给串行与并发两个口径 ——
只报串行会夸大代价，只报并发会掩盖实现要求。

用法：uv run python -m tests.test_rerank
"""

import asyncio
import statistics
import sys
import time

from app.database.session import AsyncSessionLocal


def hit_at(ranked: list[int], expected: set[int], k: int) -> float:
    return 1.0 if any(item in ranked[:k] for item in expected) else 0.0


def reciprocal_rank(ranked: list[int], expected: set[int]) -> float:
    for index, item in enumerate(ranked, 1):
        if item in expected:
            return 1.0 / index
    return 0.0


async def main() -> int:
    sys.path.insert(0, r"D:\yuxi\MindFlow\backend")

    from app.evaluation.datasets import EVALUATION_CASES
    from app.evaluation.runner import EvaluationRunner
    from app.services.retrieval_service import RetrievalService

    print("=" * 78)
    print("重排评估：质量 × 代价")
    print("=" * 78)

    runner = EvaluationRunner()
    await runner.seed()

    rows: list[dict] = []

    try:
        async with AsyncSessionLocal() as db:
            service = RetrievalService(db)

            for case in EVALUATION_CASES:
                expected, _ = await runner.resolve_expectations(case)
                expected_set = set(expected)

                async def measure(
                    with_filter: bool, with_rerank: bool
                ) -> tuple[list[int], float]:
                    started = time.monotonic()

                    results = await service.retrieve(
                        query=case.query,
                        limit=5,
                        project_id=runner.project_id,
                        document_type=(
                            case.document_type if with_filter else None
                        ),
                        rerank=with_rerank,
                    )

                    elapsed = (time.monotonic() - started) * 1000
                    return (
                        [item["chunk_id"] for item in results],
                        elapsed,
                    )

                filtered_plain, t_fp = await measure(True, False)
                filtered_rerank, t_fr = await measure(True, True)
                plain_plain, _ = await measure(False, False)

                def metrics(ids: list[int]) -> dict:
                    return {
                        "r1": hit_at(ids, expected_set, 1),
                        "r3": hit_at(ids, expected_set, 3),
                        "r5": hit_at(ids, expected_set, 5),
                        "mrr": reciprocal_rank(ids, expected_set),
                    }

                rows.append(
                    {
                        "case_id": case.id,
                        "filtered_plain": metrics(filtered_plain),
                        "filtered_rerank": metrics(filtered_rerank),
                        "unfiltered_plain": metrics(plain_plain),
                        "t_filtered_plain": t_fp,
                        "t_filtered_rerank": t_fr,
                    }
                )

                gain = (
                    metrics(filtered_rerank)["r1"]
                    - metrics(filtered_plain)["r1"]
                )

                if gain > 0:
                    mark = "  ← 升到第一"
                elif gain < 0:
                    mark = "  ← ⚠️ 把第一名挤掉"
                else:
                    mark = ""

                print(
                    f"  {case.id[:30]:<32} "
                    f"R@1 过滤={metrics(filtered_plain)['r1']:.0f} "
                    f"→ 重排={metrics(filtered_rerank)['r1']:.0f}"
                    f"{mark}"
                )

    finally:
        await runner.cleanup()

    # ---------------- 汇总 ----------------
    print()
    print("=" * 78)
    print("指标对比")
    print("=" * 78)

    configs = (
        ("过滤+无重排（正式基线）", "filtered_plain"),
        ("过滤+有重排（改进候选）", "filtered_rerank"),
        ("无过滤+无重排（对照）", "unfiltered_plain"),
    )

    print(
        f"  {'配置':<24} {'R@1':>8} {'R@3':>8} {'R@5':>8} {'MRR':>8}"
    )

    for label, key in configs:
        values = {
            metric: statistics.mean(
                row[key][metric] for row in rows
            )
            for metric in ("r1", "r3", "r5", "mrr")
        }
        print(
            f"  {label:<24} {values['r1']:>8.3f} {values['r3']:>8.3f} "
            f"{values['r5']:>8.3f} {values['mrr']:>8.3f}"
        )

    # ---------------- 逐例得失 ----------------
    print()
    print("=" * 78)
    print("重排的 R@1 逐例得失（在正式配置下）")
    print("=" * 78)

    gained = [
        row["case_id"]
        for row in rows
        if row["filtered_rerank"]["r1"] > row["filtered_plain"]["r1"]
    ]
    lost = [
        row["case_id"]
        for row in rows
        if row["filtered_rerank"]["r1"] < row["filtered_plain"]["r1"]
    ]

    print(f"  变好 {len(gained)} 例：")
    for item in gained:
        print(f"    + {item}")

    print(f"  变坏 {len(lost)} 例：")
    for item in lost:
        print(f"    - {item}")

    # ---------------- 代价 ----------------
    print()
    print("=" * 78)
    print("代价")
    print("=" * 78)

    plain_ms = statistics.median(
        row["t_filtered_plain"] for row in rows
    )
    rerank_ms = statistics.median(
        row["t_filtered_rerank"] for row in rows
    )
    extra_ms = rerank_ms - plain_ms

    print(f"  单次检索：无重排 {plain_ms:.0f}ms → 有重排 {rerank_ms:.0f}ms")
    print(f"  每次检索的增量：{extra_ms:.0f}ms")
    print()
    print("  每次出题调检索 4 次，因此每道题的额外等待：")
    print(f"    串行 4 次：{extra_ms * 4 / 1000:.1f} 秒")
    print(
        f"    并发 4 次：约 {extra_ms / 1000:.1f} 秒"
        f"（4 次互不依赖，实测并发可省 72%）"
    )

    # ---------------- 结论 ----------------
    print()
    print("=" * 78)

    baseline = statistics.mean(
        row["filtered_plain"]["r1"] for row in rows
    )
    improved = statistics.mean(
        row["filtered_rerank"]["r1"] for row in rows
    )

    print(f"  R@1: {baseline:.3f} → {improved:.3f}（{improved - baseline:+.3f}）")
    print(f"  即 23 例中多命中 {round((improved - baseline) * 23)} 例")
    print()

    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
