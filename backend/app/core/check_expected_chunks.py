"""检查"期望块"是否真的包含问题的答案。

## 为什么必须查这个

`check_rerank_regressions` 暴露了一个可疑现象：

    问题：技术面试一共几轮，怎么安排的？
    期望块 5581 的内容开头："量评估经验，能用量化指标驱动优化…"

这看起来与问题无关，而重排给了它 **0.0 分**（完全无关）。

两种可能，含义完全不同：

| 可能 | 含义 |
| --- | --- |
| A. 重排误判 | 重排有问题，该修重排 |
| B. **期望块本身就是错的** | **基线数据不可信**，所有基于它的对比都失去意义 |

第 2 种更严重：如果 marker 只是**恰好出现在某个块里**，
而那个块并不回答这个问题，那"命中该块"就不是正确的判据 ——
指标的分子从一开始就是错的。

## 它检查什么

对每个用例，打印：
- 问题
- marker
- 期望块的**完整内容**
- marker 在该块中的**位置**

据此可以人工判断"这个块是否真的回答了这个问"。

用法：uv run python -m app.core.check_expected_chunks
"""

import asyncio
import sys

from sqlalchemy import select

from app.database.session import AsyncSessionLocal

# 只看这几例 —— 它们要么被重排判成 0 分，要么是重排的回归项。
# 全量打印会淹没要害。
SUSPECT_CASES = (
    "jd_interview_rounds",
    "jd_vector_db",
    "jd_evaluation_bonus",
    "project_rrf_choice",
    "cross_strong_consistency",
)


async def main() -> int:
    sys.path.insert(0, r"D:\yuxi\MindFlow\backend")

    from app.evaluation.datasets import EVALUATION_CASES
    from app.evaluation.runner import EvaluationRunner
    from app.models.document_chunk import DocumentChunk

    runner = EvaluationRunner()
    await runner.seed()

    try:
        for case in EVALUATION_CASES:
            if case.id not in SUSPECT_CASES:
                continue

            expected, _ = await runner.resolve_expectations(case)

            print()
            print("=" * 78)
            print(f"用例 {case.id}")
            print("=" * 78)
            print(f"  问题  : {case.query}")
            print(f"  marker: {case.expected_marker!r}")
            print(f"  期望块: {expected}")

            async with AsyncSessionLocal() as db:
                rows = list(
                    (
                        await db.execute(
                            select(
                                DocumentChunk.id,
                                DocumentChunk.chunk_index,
                                DocumentChunk.content,
                            ).where(DocumentChunk.id.in_(expected))
                        )
                    ).all()
                )

            for chunk_id, index, content in rows:
                content = content or ""
                position = content.find(case.expected_marker)

                print()
                print(f"  --- chunk {chunk_id}（块序 {index}，"
                      f"{len(content)} 字符）---")
                print(
                    f"  marker 出现位置: {position}"
                    f"（{position / max(len(content), 1):.0%} 处）"
                )
                print("  完整内容:")
                for line in content.splitlines():
                    print(f"    | {line}")

    finally:
        await runner.cleanup()

    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
