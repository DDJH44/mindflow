"""寻找**最小的、不用模型的**上下文块数，使产品指标达到 1.0。

## 为什么是这个方向

`test_rerank_context.py` 给出了决定性结果：

    现状：直接取 3              22/23 = 0.957
    只扩大候选池、不重排          23/23 = 1.000
    取 10 → 重排 → 留 3         21/23 = 0.913   ← 更差

**重排在产品指标上是净负收益**（救回 0、弄丢 1），
而且单次中位 19.6 秒。因此它不该启用。

但"只扩大候选池"之所以到 1.000，是因为它**把 10 块全算了** ——
那与"取 k 块"不是同一个口径。真正要回答的是：

> **每类型取几块**，就能让答案进入上下文？

这是**零模型调用成本**的改动（只是多喂几个块给模型），
因此如果 k=4 或 5 就够，那它比重排好得多：
不花钱、不增加延迟、不引入失败模式。

## 它输出什么

对 k = 1..10 各算一次产品指标，以及**喂给模型的字符数** ——
因为上下文越长，模型越容易漏掉中间的信息
（"lost in the middle"），所以不能无限加。

用法：uv run python -m scripts.measure_context_k
"""

import asyncio
import statistics
import sys

from app.database.session import AsyncSessionLocal

DOCUMENT_TYPES = ("resume", "jd", "project", "code")

# 候选上限：一次取够，然后按前缀切出各 k
MAX_K = 10


async def main() -> int:
    sys.path.insert(0, r"D:\yuxi\MindFlow\backend")

    from app.evaluation.datasets import EVALUATION_CASES
    from app.evaluation.runner import EvaluationRunner
    from app.services.retrieval_service import RetrievalService

    runner = EvaluationRunner()
    await runner.seed()

    print("=" * 78)
    print("上下文块数 vs 产品指标（不用模型）")
    print("=" * 78)

    # k -> 命中的用例数
    hits: dict[int, int] = {k: 0 for k in range(1, MAX_K + 1)}

    # k -> 四类型合计喂给模型的字符数（各用例的中位）
    sizes: dict[int, list[int]] = {k: [] for k in range(1, MAX_K + 1)}

    try:
        async with AsyncSessionLocal() as db:
            service = RetrievalService(db)

            for case in EVALUATION_CASES:
                expected, _ = await runner.resolve_expectations(case)
                expected_set = set(expected)

                # 每类型一次取 MAX_K 个
                per_type: dict[str, list[dict]] = {}

                for document_type in DOCUMENT_TYPES:
                    per_type[document_type] = await service.retrieve(
                        query=case.query,
                        limit=MAX_K,
                        project_id=runner.project_id,
                        document_type=document_type,
                        rerank=False,
                    )

                target = case.document_type

                for k in range(1, MAX_K + 1):
                    if target is None:
                        found = any(
                            any(
                                item["chunk_id"] in expected_set
                                for item in per_type[document_type][:k]
                            )
                            for document_type in DOCUMENT_TYPES
                        )
                    else:
                        found = any(
                            item["chunk_id"] in expected_set
                            for item in per_type[target][:k]
                        )

                    if found:
                        hits[k] += 1

                    # 四类型各取 k 个的字符总量
                    total_chars = sum(
                        len(item["content"])
                        for document_type in DOCUMENT_TYPES
                        for item in per_type[document_type][:k]
                    )
                    sizes[k].append(total_chars)

    finally:
        await runner.cleanup()

    # ---------------- 汇总 ----------------
    n = len(EVALUATION_CASES)

    print()
    print("=" * 78)
    print("结果")
    print("=" * 78)
    print(
        f"  {'每类型块数':>10} {'产品指标':>10} {'命中':>8} "
        f"{'四类型字符数(中位)':>20}"
    )

    baseline_k = 3

    for k in range(1, MAX_K + 1):
        mark = ""
        if k == baseline_k:
            mark = "   ← 现状"
        elif hits[k] == n and hits[k - 1] < n:
            mark = "   ← 首次达到 1.000"

        print(
            f"  {k:>10} {hits[k] / n:>10.3f} {hits[k]:>5}/{n} "
            f"{int(statistics.median(sizes[k])):>20}{mark}"
        )

    # ---------------- 推荐 ----------------
    print()
    print("=" * 78)
    print("结论")
    print("=" * 78)

    smallest_full = next(
        (k for k in range(1, MAX_K + 1) if hits[k] == n), None
    )

    if smallest_full is None:
        print(f"  即使每类型取 {MAX_K} 块也未达到 1.000")
    else:
        print(
            f"  每类型取 **{smallest_full}** 块即可让产品指标达到 1.000"
        )
        print(
            f"  （现状取 {baseline_k} 块是 "
            f"{hits[baseline_k] / n:.3f}）"
        )
        overhead = (
            statistics.median(sizes[smallest_full])
            - statistics.median(sizes[baseline_k])
        )
        print()
        print(
            f"  代价：四类型合计多约 {int(overhead)} 字符"
            f"（现状 {int(statistics.median(sizes[baseline_k]))} → "
            f"{int(statistics.median(sizes[smallest_full]))}）"
        )
        print("  零模型调用、零额外延迟 —— 只是多喂几个块。")

    print()
    print("  ⚠️ 但块数不能无限加：上下文越长，模型越容易漏掉")
    print("     中间位置的信息。因此取「刚好够」的那个值，")
    print("     而不是取 10。")

    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
