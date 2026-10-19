"""检查重排的两处**回归**：为什么把原本正确的第一名挤掉了。

## 为什么要专门查回归

重排总体是正的（R@1 0.609 → 0.739），但有两例**变坏**：

    jd_vector_db          R@1 1 → 0
    jd_interview_rounds   R@1 1 → 0

只看净收益会掩盖"重排在某些情况下判断错了"这件事。
而如果那个判断错误有**系统性原因**，它会在更大的数据上
放大 —— 因此必须看具体是哪两个片段被调换了、为什么。

本脚本对这两例把重排前后的顺序、以及各候选的
**重排分数与原始余弦分数**都打出来。

用法：uv run python -m scripts.check_rerank_regressions
"""

import asyncio
import sys

from sqlalchemy import select

from app.database.session import AsyncSessionLocal

# 关注这两例 —— 它们是本次测量中唯一被重排弄坏的
TARGET_CASES = ("jd_vector_db", "jd_interview_rounds")


async def main() -> int:
    sys.path.insert(0, r"D:\yuxi\MindFlow\backend")

    from app.evaluation.datasets import EVALUATION_CASES
    from app.evaluation.runner import EvaluationRunner
    from app.models.document_chunk import DocumentChunk
    from app.services.rerank_service import RerankService
    from app.services.retrieval_service import RetrievalService

    runner = EvaluationRunner()
    await runner.seed()

    print("=" * 78)
    print("重排回归分析")
    print("=" * 78)

    try:
        for case in EVALUATION_CASES:
            if case.id not in TARGET_CASES:
                continue

            expected, _ = await runner.resolve_expectations(case)
            expected_set = set(expected)

            print()
            print("=" * 78)
            print(f"用例 {case.id}")
            print("=" * 78)
            print(f"  问题：{case.query}")
            print(f"  期望块：{expected}")

            async with AsyncSessionLocal() as db:
                service = RetrievalService(db)

                # 候选：带过滤取 10 个（与重排时一致）
                candidates = await service.retrieve(
                    query=case.query,
                    limit=10,
                    project_id=runner.project_id,
                    document_type=case.document_type,
                    rerank=False,
                )

                reranked = await RerankService().rerank(
                    query=case.query,
                    candidates=candidates,
                    top_k=5,
                    seed=0,
                )

                # 取各候选的正文，以便人工判断"哪个才真的回答了这个问"
                chunk_ids = [item["chunk_id"] for item in candidates]

                rows = list(
                    (
                        await db.execute(
                            select(
                                DocumentChunk.id,
                                DocumentChunk.content,
                            ).where(DocumentChunk.id.in_(chunk_ids))
                        )
                    ).all()
                )

                contents = {row[0]: row[1] or "" for row in rows}

            rerank_scores = {
                item["chunk_id"]: item.get("rerank_score")
                for item in reranked
            }

            print()
            print("  候选（按余弦顺序），★ = 期望块：")
            print(
                f"    {'序':>3} {'chunk':>7} {'余弦':>8} "
                f"{'重排分':>8}  内容前 60 字"
            )

            for index, item in enumerate(candidates, 1):
                chunk_id = item["chunk_id"]
                mark = "★" if chunk_id in expected_set else " "
                score = rerank_scores.get(chunk_id)
                score_text = (
                    f"{score:.1f}" if score is not None else "—"
                )
                preview = " ".join(
                    contents.get(chunk_id, "")[:60].split()
                )

                print(
                    f"    {mark}{index:>2} {chunk_id:>7} "
                    f"{item['score']:>+8.4f} {score_text:>8}  "
                    f"{preview}"
                )

            print()
            print("  重排后的前 5：")
            for index, item in enumerate(reranked, 1):
                chunk_id = item["chunk_id"]
                mark = "★" if chunk_id in expected_set else " "
                print(
                    f"    {mark}{index}. chunk {chunk_id}"
                )

    finally:
        await runner.cleanup()

    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
