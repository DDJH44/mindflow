"""测量检索分数的分布 —— 用于**有依据地**选相似度阈值。

## 为什么必须先测量

"加一个相似度阈值"听起来是明显的改进，但阈值取多少必须来自数据：

- 取太高 → 把正确答案也滤掉，召回率下降
- 取太低 → 等于没滤，白增加复杂度

而这个项目里还有一个**更基础的问题必须先查清**：
Milvus 在 `metric_type="COSINE"` 下返回的 `distance` 到底是什么范围？

- 若是**余弦相似度**（越大越像），范围 [-1, 1]
- 若是**余弦距离**（越小越像），范围 [0, 2]

猜错方向会让阈值完全失效（甚至反向）。
因此脚本把实际观察到的范围与**已知命中/未命中**的分数对比着打出来。

## 它输出什么

对每个评估用例，按 top-k 顺序列出分数的命中情况：

    rank  chunk_id  score    是否命中目标
    1     5119      0.7321   ✓
    2     5122      0.4102
    ...

据此可以判断"命中的分数"与"未命中的分数"是否有清晰的分界。

用法：uv run python -m scripts.measure_retrieval_scores
"""

import asyncio
import statistics
import sys

from app.database.session import AsyncSessionLocal


async def main() -> int:
    sys.path.insert(0, r"D:\yuxi\MindFlow\backend")

    from app.evaluation.datasets import DOCUMENTS, EVALUATION_CASES
    from app.evaluation.runner import EvaluationRunner
    from app.services.retrieval_service import RetrievalService

    print("=" * 78)
    print("检索分数分布测量")
    print("=" * 78)

    runner = EvaluationRunner()
    await runner.seed()

    hit_scores: list[float] = []
    miss_scores: list[float] = []
    per_case: list[tuple[str, list[tuple[int, float, bool]]]] = []

    try:
        async with AsyncSessionLocal() as db:
            service = RetrievalService(db)

            for case in EVALUATION_CASES:
                document_id, chunk_ids = runner.seeded[
                    case.document_name
                ]
                expected = set(chunk_ids)

                results = await service.retrieve(
                    query=case.query,
                    limit=10,  # 多取一些，看分数在更远处怎么衰减
                    project_id=runner.project_id,
                )

                rows: list[tuple[int, float, bool]] = []

                for item in results:
                    is_hit = item["chunk_id"] in expected
                    rows.append(
                        (item["chunk_id"], item["score"], is_hit)
                    )

                    if is_hit:
                        hit_scores.append(item["score"])
                    else:
                        miss_scores.append(item["score"])

                per_case.append((case.query, rows))

    finally:
        await runner.cleanup()

    # ---------------- 逐用例 ----------------
    print()
    print("逐用例（top-10，★ 表示命中目标块）")
    print("=" * 78)

    for query, rows in per_case:
        print()
        print(f"  {query[:56]}")

        for rank, (chunk_id, score, is_hit) in enumerate(rows, 1):
            mark = "★" if is_hit else " "
            print(
                f"    {rank:>2}. {mark} chunk {chunk_id:<6} "
                f"score={score:+.4f}"
            )

    # ---------------- 分布 ----------------
    print()
    print("=" * 78)
    print("分数分布")
    print("=" * 78)

    def describe(label: str, values: list[float]) -> None:
        if not values:
            print(f"  {label}: 无样本")
            return

        values = sorted(values)
        print(f"  {label}（{len(values)} 个）:")
        print(
            f"    min={values[0]:+.4f}  "
            f"p25={values[len(values) // 4]:+.4f}  "
            f"中位={statistics.median(values):+.4f}  "
            f"p75={values[len(values) * 3 // 4]:+.4f}  "
            f"max={values[-1]:+.4f}"
        )

    describe("命中目标的分数", hit_scores)
    describe("未命中的分数", miss_scores)

    if hit_scores and miss_scores:
        print()
        print(f"  命中最低分: {min(hit_scores):+.4f}")
        print(f"  未命中最高分: {max(miss_scores):+.4f}")

        overlap_low = min(hit_scores)
        overlap_high = max(miss_scores)

        print()
        if overlap_low > overlap_high:
            print("  ✓ 两组**完全不重叠** —— 存在一个能完美分开的阈值，")
            print(
                f"     区间为 ({overlap_high:+.4f}, {overlap_low:+.4f}]"
            )
        else:
            print("  ⚠️ 两组**有重叠** —— 没有一个阈值能完美分开。")
            print(
                f"     重叠区间为 [{overlap_low:+.4f}, {overlap_high:+.4f}]"
            )
            print()
            print("     这意味着：阈值只能做**权衡**（滤掉部分噪声，")
            print("     同时也会滤掉部分正确答案），而不能无损地提升。")

        # 尝试若干阈值，报告各自的"滤掉多少噪声/误伤多少正确"
        print()
        print("  候选阈值的效果（只统计 top-5 之内）:")
        print(
            f"    {'阈值':>8}  {'滤掉噪声':>10}  {'误伤正确':>10}"
        )

        for threshold in (
            0.20, 0.25, 0.30, 0.35, 0.40, 0.45, 0.50,
        ):
            noise_removed = sum(
                1 for value in miss_scores if value < threshold
            )
            hits_lost = sum(
                1 for value in hit_scores if value < threshold
            )
            print(
                f"    {threshold:>8.2f}  {noise_removed:>10}  "
                f"{hits_lost:>10}"
            )

    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
