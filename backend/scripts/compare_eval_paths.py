"""在同一进程内对比"评估器的检索"与"我的直接检索"。

## 为什么需要它

两处测出的 R@1 不一致（正式基线 0.609 vs 我的脚本 0.522），
而这是 **2 例**的差距 —— 在查清之前，"重排提升了多少"
这类结论都不可靠。

已排除的因素：
- **top-k**：`limit=5` 与 `limit=10` 给出完全相同的 R@1（0.522）
- **期望块数量**：23 例全部只有 1 个期望块

已发现的**唯一传入参数差异**：
`EvaluationRunner.run_case` 调检索时传了
`document_type=case.document_type`，而我的脚本没传。

本脚本在同一份播种数据上，同时按"带过滤/不带过滤"各检索一次，
逐例对比 —— 谁对谁错就此确定。

用法：uv run python -m scripts.compare_eval_paths
"""

import asyncio
import sys

from app.database.session import AsyncSessionLocal


def hit_at(ranked: list[int], expected: set[int], k: int) -> float:
    return 1.0 if any(item in ranked[:k] for item in expected) else 0.0


async def main() -> int:
    sys.path.insert(0, r"D:\yuxi\MindFlow\backend")

    from app.evaluation.datasets import EVALUATION_CASES
    from app.evaluation.runner import EvaluationRunner
    from app.services.retrieval_service import RetrievalService

    runner = EvaluationRunner()
    await runner.seed()

    print("=" * 78)
    print("两条检索路径逐例对比")
    print("=" * 78)
    print("  A = 评估器路径（传 document_type 过滤）")
    print("  B = 直接路径（不传过滤）")
    print()

    rows = []

    try:
        for case in EVALUATION_CASES:
            expected, _ = await runner.resolve_expectations(case)
            expected_set = set(expected)

            async with AsyncSessionLocal() as db:
                service = RetrievalService(db)

                # A：与 run_case 完全一致的调用
                path_a = await service.retrieve(
                    query=case.query,
                    limit=5,
                    project_id=runner.project_id,
                    document_type=case.document_type,
                )

                # B：不带类型过滤
                path_b = await service.retrieve(
                    query=case.query,
                    limit=5,
                    project_id=runner.project_id,
                )

            a_ids = [item["chunk_id"] for item in path_a]
            b_ids = [item["chunk_id"] for item in path_b]

            rows.append(
                {
                    "case_id": case.id,
                    "document_type": case.document_type,
                    "expected": expected,
                    "a": a_ids,
                    "b": b_ids,
                    "r1_a": hit_at(a_ids, expected_set, 1),
                    "r1_b": hit_at(b_ids, expected_set, 1),
                }
            )

            mark = ""

            if rows[-1]["r1_a"] != rows[-1]["r1_b"]:
                mark = "  ← ⚠️ 两条路径结论不同"

            print(
                f"  {case.id[:30]:<32} "
                f"type={str(case.document_type):<9} "
                f"期望={expected} "
                f"A={rows[-1]['r1_a']:.0f} B={rows[-1]['r1_b']:.0f}"
                f"{mark}"
            )

    finally:
        await runner.cleanup()

    print()
    print("=" * 78)
    print("汇总")
    print("=" * 78)

    n = len(rows)

    for label, key in (("A（带过滤）", "r1_a"), ("B（不带过滤）", "r1_b")):
        value = sum(row[key] for row in rows) / n
        print(f"  {label:<16} R@1 = {value:.3f}")

    differing = [
        row for row in rows if row["r1_a"] != row["r1_b"]
    ]

    print()
    print(f"  结论不同的用例：{len(differing)} 个")

    for row in differing:
        print(f"    {row['case_id']}（type={row['document_type']}）")
        print(f"      期望 {row['expected']}")
        print(f"      A 检索到 {row['a']}")
        print(f"      B 检索到 {row['b']}")

    print()
    print("  按 document_type 分组：")

    from collections import defaultdict

    grouped: dict = defaultdict(list)

    for row in rows:
        grouped[str(row["document_type"])].append(row)

    for key, items in sorted(grouped.items()):
        a = sum(item["r1_a"] for item in items) / len(items)
        b = sum(item["r1_b"] for item in items) / len(items)
        print(
            f"    {key:<10} {len(items):>2} 例  "
            f"A={a:.2f}  B={b:.2f}"
        )

    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
