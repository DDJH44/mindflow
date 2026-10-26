"""一次性探针：用真实模型跑一次整场评价，看 findings 是否可用。"""

import asyncio
import sys

from sqlalchemy import text

from app.database.session import AsyncSessionLocal


async def main() -> int:
    sys.path.insert(0, r"D:\yuxi\MindFlow\backend")

    from app.services.interview.evaluator import InterviewEvaluator
    from app.services.interview.interview_state_machine import (
        normalize_status,
    )

    import argparse

    parser = argparse.ArgumentParser(
        description="用真实模型跑一次整场评价，验证可追溯结论"
    )
    parser.add_argument("--session", type=int, default=968)
    session_id = parser.parse_args().session

    async with AsyncSessionLocal() as db:
        rows = list(
            (
                await db.execute(
                    text(
                        """
                        SELECT q.id, q.question, q.question_index, a.answer
                        FROM interview_questions q
                        LEFT JOIN interview_answers a
                          ON a.question_id = q.id
                        WHERE q.session_id = :s
                        ORDER BY q.question_index
                        """
                    ),
                    {"s": session_id},
                )
            ).all()
        )

    if not rows:
        print("  没有题目")
        return 1

    # 拼成 evaluator 期望的形状：(question, answer)
    class Question:
        def __init__(self, question: str) -> None:
            self.question = question

    class Answer:
        def __init__(self, answer: str) -> None:
            self.answer = answer

    turns = [
        (Question(row[1]), Answer(row[3]) if row[3] else None)
        for row in rows
    ]

    from app.services.interview.evaluation_service import (
        InterviewEvaluationService,
    )

    content = InterviewEvaluationService._build_interview_content(turns)

    print("=" * 74)
    print("评价内容（前 400 字符）")
    print("=" * 74)
    print(content[:400])
    print()

    answer_count = sum(1 for _q, a in turns if a is not None)

    evaluation = await InterviewEvaluator().evaluate(
        interview_content=content,
        repeats=1,
        turn_count=answer_count,
        max_turn=len(turns),
    )

    print("=" * 74)
    print("模型给出的可追溯结论")
    print("=" * 74)
    print(f"  strengths   : {len(evaluation.strengths)} 条")
    print(f"  weaknesses  : {len(evaluation.weaknesses)} 条")
    print(f"  suggestions : {len(evaluation.suggestions)} 条")
    print(f"  findings    : {len(evaluation.findings)} 条")
    print()

    verified = 0

    for finding in evaluation.findings:
        mark = "✓" if finding.verified else "✗"
        print(
            f"  [{mark}] {finding.kind:<10} "
            f"turns={finding.turns}  {finding.text[:60]}"
        )

        if finding.verified:
            verified += 1

    print()
    print(f"  可追溯（verified）: {verified} / {len(evaluation.findings)}")

    # 每条 verified 的 findings 必须能在对应列表里找到原文
    mismatched = [
        finding.text
        for finding in evaluation.findings
        if finding.verified
        and (
            finding.text not in evaluation.strengths
            and finding.text not in evaluation.weaknesses
            and finding.text not in evaluation.suggestions
        )
    ]

    print(f"  与列表原文不一致的: {len(mismatched)}")

    if mismatched:
        print("  [!!] 有出处挂不到列表条目上：")
        for item in mismatched:
            print(f"    - {item[:60]}")

        return 1

    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
