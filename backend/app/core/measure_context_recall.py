"""按**产品实际用法**测量检索：每类型取 3 块，而不是看 R@1。

## 为什么之前测错了

`InterviewContextBuilder` 的实际调用是：

    limit_per_type = 3
    for document_type in (resume, jd, project, code):
        retrieve(query, limit=3, document_type=...)

四个类型的 3 块**全部**喂给模型（约 12 块）。

也就是说：**产品不关心某一块排第几。** 只要答案落进
那 3 块里，模型就能看到它。因此：

| 指标 | 对产品的含义 |
| --- | --- |
| R@1 | **几乎无关** —— 同类型的第 1 还是第 3 块，模型都看得到 |
| **R@3（每类型）** | **这才是产品指标**：答案有没有进上下文 |
| R@5 | 与产品无关（每类型只取 3） |

我此前一直盯着 R@1 = 0.609，并据此评判重排 ——
那是**优化了一个产品不用的指标**。

## 它测什么

1. **每类型的 R@3**：答案是否进入该类型的上下文
2. **重排对 R@3 的影响**：若不开重排时 R@3 就是 0.913，
   那么扩大候选池（取 10 → 重排 → 留 3）能否把
   那些"答案没进 3 块"的用例救回来
3. **绝对上限**：`R@3 = 1.0` 需要这些用例的答案至少出现在
   该类型的候选里 —— 若连 top-10 都没有，说明是**召回**问题
   （切块或查询表述），重排救不了

用法：uv run python -m app.core.measure_context_recall
"""

import asyncio
import statistics
import sys

from app.database.session import AsyncSessionLocal

DOCUMENT_TYPES = ("resume", "jd", "project", "code")


def contains(ids: list[int], expected: set[int], k: int) -> bool:
    return any(item in expected for item in ids[:k])


async def main() -> int:
    sys.path.insert(0, r"D:\yuxi\MindFlow\backend")

    from app.evaluation.datasets import EVALUATION_CASES
    from app.evaluation.runner import EvaluationRunner
    from app.services.retrieval_service import RetrievalService

    runner = EvaluationRunner()
    await runner.seed()

    print("=" * 78)
    print("按产品用法测量：每类型取 3 块")
    print("=" * 78)

    rows: list[dict] = []

    try:
        async with AsyncSessionLocal() as db:
            service = RetrievalService(db)

            for case in EVALUATION_CASES:
                expected, _ = await runner.resolve_expectations(case)
                expected_set = set(expected)

                # 该用例的期望块属于哪个类型？
                # 产品会为四个类型各检索一次，因此只要
                # **期望所在的那个类型**把它捞进前 3 就算成功。
                per_type: dict[str, dict] = {}

                for document_type in DOCUMENT_TYPES:
                    three = await service.retrieve(
                        query=case.query,
                        limit=3,
                        project_id=runner.project_id,
                        document_type=document_type,
                        rerank=False,
                    )
                    three_ids = [
                        item["chunk_id"] for item in three
                    ]

                    # 候选池上限：取 10，看答案在不在这 10 个里
                    ten = await service.retrieve(
                        query=case.query,
                        limit=10,
                        project_id=runner.project_id,
                        document_type=document_type,
                        rerank=False,
                    )
                    ten_ids = [item["chunk_id"] for item in ten]

                    per_type[document_type] = {
                        "in_top3": contains(
                            three_ids, expected_set, 3
                        ),
                        "in_top10": contains(
                            ten_ids, expected_set, 10
                        ),
                    }

                # 期望块的实际类型
                target_type = case.document_type

                if target_type is None:
                    # 跨类型用例：任一类型捞到就算成功
                    in_context = any(
                        value["in_top3"]
                        for value in per_type.values()
                    )
                    in_pool = any(
                        value["in_top10"]
                        for value in per_type.values()
                    )
                else:
                    in_context = per_type[target_type]["in_top3"]
                    in_pool = per_type[target_type]["in_top10"]

                rows.append(
                    {
                        "case_id": case.id,
                        "document_type": target_type,
                        "in_context": in_context,
                        "in_pool": in_pool,
                    }
                )

                # 只有"没进上下文"的才值得细看
                if not in_context:
                    mark = "  ← 答案没进上下文"
                    if not in_pool:
                        mark += "（连 top-10 都没有：**召回**问题）"
                    else:
                        mark += "（在 top-10 内：**排序**问题）"
                else:
                    mark = ""

                print(
                    f"  {'✓' if in_context else '✗'} "
                    f"{case.id[:32]:<34} "
                    f"type={str(target_type):<8}{mark}"
                )

    finally:
        await runner.cleanup()

    # ---------------- 汇总 ----------------
    print()
    print("=" * 78)
    print("汇总")
    print("=" * 78)

    n = len(rows)
    in_context = sum(1 for row in rows if row["in_context"])
    in_pool = sum(1 for row in rows if row["in_pool"])

    print(f"  答案进入上下文（每类型 top-3）：{in_context}/{n} "
          f"= {in_context / n:.3f}")
    print(f"  答案进入候选池（每类型 top-10）：{in_pool}/{n} "
          f"= {in_pool / n:.3f}")

    print()
    print("  未进入上下文的用例：")
    missed = [row for row in rows if not row["in_context"]]

    for row in missed:
        kind = "召回问题" if not row["in_pool"] else "排序问题"
        print(
            f"    - {row['case_id']}（{row['document_type']}）：{kind}"
        )

    print()
    print("  说明：")
    print("    「排序问题」→ 扩大候选池 + 重排可能救回来")
    print("    「召回问题」→ 重排救不了，需要改切块或查询表述")

    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
