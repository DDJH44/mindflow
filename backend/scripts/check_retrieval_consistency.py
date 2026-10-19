"""核对：为什么"不开重排"的 R@1 在我的脚本里是 0.522，
而正式基线报 0.609？

## 为什么必须查清

同一份数据集、同一个模型，两次测量差 0.087（2 例）。
在这两个数不一致之前，**任何"改进了多少"的说法都不可靠** ——
包括我刚测出的"重排把 R@1 从 0.522 提到 0.826"。

## 要排除的几种可能

1. **top-k 差异**：我的脚本取候选时用了 `limit=10`，
   而正式基线是 `limit=5`。若 `limit` 会改变返回顺序或集合，
   指标就会不同。
2. **期望块解析差异**：`resolve_expectations` 只对**文档自身**
   的块匹配 marker；而正式基线可能用了别的口径。
3. **语料重新播种**：每次 `seed()` 都新建项目与块，
   若 marker 在不同次播种中落到不同块，就可能对不上。

本脚本把这三种可能逐一验证：**在同一次播种内**，
分别用 limit=5 / limit=10 检索，并打印每个用例的
"期望块"与"是否命中"。

用法：uv run python -m scripts.check_retrieval_consistency
"""

import asyncio
import sys

from app.database.session import AsyncSessionLocal


def hit_at(ranked: list[int], expected: set[int], k: int) -> float:
    return 1.0 if any(item in expected for item in ranked[:k]) else 0.0


async def main() -> int:
    sys.path.insert(0, r"D:\yuxi\MindFlow\backend")

    from app.evaluation.datasets import EVALUATION_CASES
    from app.evaluation.runner import EvaluationRunner
    from app.services.retrieval_service import RetrievalService

    runner = EvaluationRunner()
    await runner.seed()

    print("=" * 78)
    print("检索一致性核对（同一次播种内）")
    print("=" * 78)

    rows = []

    try:
        for case in EVALUATION_CASES:
            expected, document_only = await runner.resolve_expectations(
                case
            )

            async with AsyncSessionLocal() as db:
                service = RetrievalService(db)

                five = await service.retrieve(
                    query=case.query,
                    limit=5,
                    project_id=runner.project_id,
                    rerank=False,
                )

                ten = await service.retrieve(
                    query=case.query,
                    limit=10,
                    project_id=runner.project_id,
                    rerank=False,
                )

            five_ids = [item["chunk_id"] for item in five]
            ten_ids = [item["chunk_id"] for item in ten]

            expected_set = set(expected)

            rows.append(
                {
                    "case_id": case.id,
                    "expected": expected,
                    "five": five_ids,
                    "ten": ten_ids,
                    "r1_five": hit_at(five_ids, expected_set, 1),
                    "r1_ten": hit_at(ten_ids, expected_set, 1),
                    "prefix_same": ten_ids[:5] == five_ids,
                    "document_only": document_only,
                }
            )

            mark = ""

            if rows[-1]["r1_five"] != rows[-1]["r1_ten"]:
                mark = "  ← ⚠️ limit 不同导致命中不同"
            elif not rows[-1]["prefix_same"]:
                mark = "  ← limit 改变了顺序"

            print(
                f"  {case.id[:32]:<34} "
                f"期望={expected} "
                f"R@1(5)={rows[-1]['r1_five']:.0f} "
                f"R@1(10)={rows[-1]['r1_ten']:.0f}"
                f"{mark}"
            )

    finally:
        await runner.cleanup()

    # ---------------- 汇总 ----------------
    print()
    print("=" * 78)
    print("汇总")
    print("=" * 78)

    n = len(rows)

    for label, key in (("limit=5", "r1_five"), ("limit=10", "r1_ten")):
        value = sum(row[key] for row in rows) / n
        print(f"  {label:<10} R@1 = {value:.3f}")

    mismatches = [
        row for row in rows if not row["prefix_same"]
    ]

    print()
    print(
        f"  前 5 名不一致的用例：{len(mismatches)} 个"
    )
    for row in mismatches:
        print(f"    {row['case_id']}")
        print(f"      limit=5 : {row['five']}")
        print(f"      limit=10: {row['ten'][:5]}")

    document_only = [
        row for row in rows if row["document_only"]
    ]
    if document_only:
        print()
        print("  仅适合文档级指标的用例（marker 跨块）:")
        for row in document_only:
            print(f"    {row['case_id']}")

    print()
    print("  期望块数量分布:")
    from collections import Counter

    for size, count in sorted(
        Counter(len(row["expected"]) for row in rows).items()
    ):
        print(f"    {size} 个期望块：{count} 例")

    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
