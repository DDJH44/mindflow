"""重排的**代价**测量：不同截断长度下的延迟与质量。

## 为什么单独测这个

主对比（`test_rerank.py`）证明了重排把 R@1 从 0.522 提到 0.826 ——
质量上明确有效。但同一份测量也给出 **中位 10.5 秒/次**，
而每次出题调检索 4 次 → 每道题多 42 秒。

那个代价不可接受。而**延迟的主因是输入长度**，
因此这里测"截断到多少字符"能在保住质量的前提下降下来。

## 它同时测并发

即使单次降到 3 秒，4 次串行仍是 12 秒。这 4 次之间**互不依赖**，
因此可以并发 —— 那能把总代价压到接近单次耗时。
本脚本测串行 4 次与并发 4 次的差别，用来判断"该不该改成并发"。

用法：uv run python -m tests.test_rerank_cost
"""

import asyncio
import statistics
import sys
import time

from app.database.session import AsyncSessionLocal

# 要对比的截断长度。
#
# 800 是原值（已实测 10.5 秒）；往下测是为了找到
# "质量不降、延迟明显下降"的那个点。
CONTENT_LIMITS = (800, 400, 300, 150)


def hit_at(ranked: list[int], expected: set[int], k: int) -> float:
    return 1.0 if any(item in expected for item in ranked[:k]) else 0.0


async def main() -> int:
    sys.path.insert(0, r"D:\yuxi\MindFlow\backend")

    from app.evaluation.datasets import EVALUATION_CASES
    from app.evaluation.runner import EvaluationRunner
    from app.services.rerank_service import RerankService
    from app.services.retrieval_service import RetrievalService

    print("=" * 78)
    print("重排代价测量")
    print("=" * 78)

    runner = EvaluationRunner()
    await runner.seed()

    # 缓存候选：每个用例只embed一次，然后对各个截断长度复用。
    # 否则测出的延迟会把 embedding 也算进去，混淆结论。
    candidates_by_case: dict[str, tuple[list[dict], set[int]]] = {}

    try:
        expectations: dict[str, list[int]] = {}

        for case in EVALUATION_CASES:
            chunk_ids, _ = await runner.resolve_expectations(case)
            expectations[case.id] = chunk_ids

        async with AsyncSessionLocal() as db:
            service = RetrievalService(db)

            for case in EVALUATION_CASES:
                plain = await service.retrieve(
                    query=case.query,
                    limit=10,
                    project_id=runner.project_id,
                    rerank=False,
                )
                candidates_by_case[case.id] = (
                    plain,
                    set(expectations[case.id]),
                )

    finally:
        await runner.cleanup()

    reranker = RerankService()

    # ---------------- 各截断长度 ----------------
    print()
    print("=" * 78)
    print("截断长度 → 延迟与质量")
    print("=" * 78)
    print(
        f"  {'截断':>6} {'中位延迟':>10} {'最大延迟':>10} "
        f"{'R@1':>8} {'R@3':>8} {'MRR':>8}"
    )

    summary: dict[int, dict] = {}

    for limit in CONTENT_LIMITS:
        latencies: list[float] = []
        r1: list[float] = []
        r3: list[float] = []
        mrr: list[float] = []

        for case in EVALUATION_CASES:
            candidates, expected = candidates_by_case[case.id]

            started = time.monotonic()

            ordered = await reranker.rerank(
                query=case.query,
                candidates=candidates,
                top_k=5,
                seed=0,
                content_limit=limit,
            )

            latencies.append((time.monotonic() - started) * 1000)

            ids = [item["chunk_id"] for item in ordered]
            r1.append(hit_at(ids, expected, 1))
            r3.append(hit_at(ids, expected, 3))

            for index, item in enumerate(ids, 1):
                if item in expected:
                    mrr.append(1.0 / index)
                    break
            else:
                mrr.append(0.0)

        summary[limit] = {
            "median_ms": statistics.median(latencies),
            "max_ms": max(latencies),
            "r1": statistics.mean(r1),
            "r3": statistics.mean(r3),
            "mrr": statistics.mean(mrr),
        }

        print(
            f"  {limit:>6} {statistics.median(latencies):>9.0f}ms "
            f"{max(latencies):>9.0f}ms "
            f"{statistics.mean(r1):>8.3f} "
            f"{statistics.mean(r3):>8.3f} "
            f"{statistics.mean(mrr):>8.3f}"
        )

    # ---------------- 串行 vs 并发 ----------------
    print()
    print("=" * 78)
    print("串行 4 次 vs 并发 4 次（模拟每次出题调检索 4 次）")
    print("=" * 78)

    # 取 4 个不同用例的候选，模拟 4 种材料类型的检索
    sample = [
        (case.query, candidates_by_case[case.id][0])
        for case in EVALUATION_CASES[:4]
    ]

    # 用当前默认截断（300）
    started = time.monotonic()

    for query, candidates in sample:
        await reranker.rerank(
            query=query, candidates=candidates, top_k=5, seed=0
        )

    serial_ms = (time.monotonic() - started) * 1000

    started = time.monotonic()

    await asyncio.gather(
        *(
            reranker.rerank(
                query=query, candidates=candidates, top_k=5, seed=0
            )
            for query, candidates in sample
        )
    )

    parallel_ms = (time.monotonic() - started) * 1000

    print(f"  串行 4 次：{serial_ms:.0f}ms")
    print(f"  并发 4 次：{parallel_ms:.0f}ms")
    print(
        f"  并发带来的缩减：{serial_ms - parallel_ms:.0f}ms"
        f"（{100 * (1 - parallel_ms / serial_ms):.0f}%）"
    )

    # ---------------- 结论 ----------------
    print()
    print("=" * 78)
    print("结论")
    print("=" * 78)

    best = min(summary.items(), key=lambda item: item[1]["median_ms"])
    best_quality = max(
        summary.items(), key=lambda item: item[1]["r1"]
    )

    print(
        f"  最快：截断 {best[0]} 字符 → 中位 "
        f"{best[1]['median_ms']:.0f}ms，R@1={best[1]['r1']:.3f}"
    )
    print(
        f"  质量最好：截断 {best_quality[0]} 字符 → R@1="
        f"{best_quality[1]['r1']:.3f}，中位 "
        f"{best_quality[1]['median_ms']:.0f}ms"
    )
    print()
    print("  对照：不开重排的 R@1 = 0.522（本套数据、同一批候选）")

    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
