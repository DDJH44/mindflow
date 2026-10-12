"""审计评估数据集：每个用例的 marker 是否**真的回答**了它的问题。

## 为什么必须做这件事

`check_rerank_regressions` 暴露了一个严重问题：

    用例 jd_evaluation_bonus
    问题  : 什么样的评估经验算加分项？
    marker: '良好的沟通能力，能向非技术角色解释技术取舍。'

marker 指向的是"任职要求六、具备良好的沟通能力" ——
**而它不回答那个问题**。语料里真正的加分项是
"有检索增强系统的检索质量评估经验，能用量化指标驱动优化"。

`resolve_expectations` 用的是**纯子串匹配**：marker 出现在哪块
就认哪块。**没有任何机制保证那个块真的回答了那个问题。**

因此如果 marker 指错，"命中该块"就不是正确的判据 ——
**指标的分子从一开始就是错的**，所有基于它的对比（包括
"重排提升了多少"）都失去意义。

## 一个自动化的辅助判据

完全靠人工核对 23 例容易漏。这里加一个**廉价的自动信号**：

> 用 marker 所在块的正文去问 LLM："这段文字能回答这个问题吗？"

对判断为"不能"的用例，把完整信息打出来**供人工复核**。
⚠️ 这**不是**自动改数据 —— LLM 的判断也会错，
所以它只用来**缩小人工检查的范围**，最终仍由人决定。

用法：uv run python -m app.core.audit_eval_dataset
"""

import asyncio
import sys

from sqlalchemy import select

from app.database.session import AsyncSessionLocal


async def main() -> int:
    sys.path.insert(0, r"D:\yuxi\MindFlow\backend")

    from app.evaluation.datasets import EVALUATION_CASES
    from app.evaluation.runner import EvaluationRunner
    from app.models.document_chunk import DocumentChunk
    from app.services.openai_llm_service import OpenAILLMService

    runner = EvaluationRunner()
    await runner.seed()

    llm = OpenAILLMService()

    # 判定为"不回答"的用例，最后集中列出
    suspicious: list[str] = []

    print("=" * 78)
    print("评估数据集审计：marker 是否真的回答问题")
    print("=" * 78)

    try:
        for case in EVALUATION_CASES:
            expected, document_only = await runner.resolve_expectations(
                case
            )

            # marker 出现在几个块里？
            # 出现多次说明它跨块，或语料里有重复表述 ——
            # 前者会让"命中哪块算对"变得没有定义。
            async with AsyncSessionLocal() as db:
                _doc_id, all_chunk_ids = runner.seeded[
                    case.document_name
                ]

                rows = list(
                    (
                        await db.execute(
                            select(
                                DocumentChunk.id, DocumentChunk.content
                            ).where(
                                DocumentChunk.id.in_(all_chunk_ids)
                            )
                        )
                    ).all()
                )

            matching = [
                row[0]
                for row in rows
                if case.expected_marker in (row[1] or "")
            ]

            if len(expected) != 1 or len(matching) != 1:
                print()
                print(f"  ⚠️ {case.id}")
                print(
                    f"     marker 出现在 {len(matching)} 个块里，"
                    f"resolve_expectations 返回 {len(expected)} 个"
                )
                print(f"     marker: {case.expected_marker[:50]!r}")
                suspicious.append(case.id)
                continue

            chunk_id = expected[0]

            content = next(
                (row[1] or "" for row in rows if row[0] == chunk_id),
                "",
            )

            if not content:
                print(f"  ⚠️ {case.id}: 找不到期望块正文")
                suspicious.append(case.id)
                continue

            # 让模型判断"这段能否回答这个问题"
            verdict = await llm.generate(
                prompt=(
                    f"问题：{case.query}\n\n"
                    f"一段材料：\n{content[:900]}\n\n"
                    "这段材料里**是否包含能直接回答该问题的信息**？\n"
                    "只回答一个词：能，或不能。"
                ),
                system_prompt=(
                    "你是严格的事实核查员。只有当材料里**明确写出**了"
                    "问题所问的那个具体事实时，才回答「能」。"
                    "仅仅是同一主题、或相关背景，都回答「不能」。"
                ),
                temperature=0.0,
                max_tokens=10,
            )

            can_answer = "能" in verdict[:4] and "不能" not in verdict[:4]

            mark = "✓" if can_answer else "✗"
            print(
                f"  [{mark}] {case.id[:34]:<36} "
                f"{case.query[:30]}"
            )

            if not can_answer:
                suspicious.append(case.id)

                print(f"        判定：{verdict.strip()[:10]}")
                print(
                    f"        marker: {case.expected_marker[:60]!r}"
                )

                # 把该块里最像答案的部分打出来供人工看
                preview = " ".join(content.split())[:220]
                print(f"        块内容: {preview}")

    finally:
        await runner.cleanup()

    print()
    print("=" * 78)
    print("汇总")
    print("=" * 78)
    print(
        f"  可疑用例 {len(set(suspicious))} / {len(EVALUATION_CASES)}"
    )

    for case_id in sorted(set(suspicious)):
        print(f"    - {case_id}")

    if suspicious:
        print()
        print("  ⚠️ 这些用例的 marker 可能没有指向真正的答案 ——")
        print("     需要人工核对并修正，否则基于本数据集的指标不可信。")
        print("     （本脚本只是缩小检查范围，不自动改数据：")
        print("      LLM 的判断本身也会错。）")

    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
