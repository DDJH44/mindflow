"""查看一场面试的作答与评分详情，用于判断评分是否合理。"""

import asyncio
import sys

from sqlalchemy import text

from app.database.session import AsyncSessionLocal


async def main() -> int:
    sys.path.insert(0, r"D:\yuxi\MindFlow\backend")

    async with AsyncSessionLocal() as db:
        rows = list(
            (
                await db.execute(
                    text(
                        """
                        SELECT s.id, s.status, s.termination_reason,
                               s.questions_asked, s.max_questions,
                               s.created_at
                        FROM interview_sessions s
                        WHERE s.status = 'completed'
                        ORDER BY s.updated_at DESC
                        LIMIT 3
                        """
                    )
                )
            ).all()
        )

        print()
        print(f"  最近完成的会话 {len(rows)} 个：")

        for row in rows:
            print(
                f"    id={row[0]} 状态={row[1]} "
                f"结束原因={row[2]} 题数={row[3]}/{row[4]} "
                f"{row[5]:%m-%d %H:%M}"
            )

        for row in rows:
            session_id = row[0]
            print()
            print("=" * 74)
            print(f"会话 {session_id} 的作答")
            print("=" * 74)

            questions = list(
                (
                    await db.execute(
                        text(
                            """
                            SELECT q.question_index, q.question,
                                   a.answer
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

            for index, question, answer in questions:
                print(f"  --- 第 {index + 1} 题 ---")
                print(f"    问: {str(question)[:100]}")

                if answer is None:
                    print("    答: （未作答）")
                else:
                    print(f"    答: {str(answer)[:160]}")

            evaluation = (
                await db.execute(
                    text(
                        """
                        SELECT overall_score, technical_score,
                               project_score, communication_score,
                               overall_anchor, technical_anchor,
                               project_anchor, communication_anchor,
                               anchor_reason
                        FROM interview_evaluations
                        WHERE session_id = :s
                        LIMIT 1
                        """
                    ),
                    {"s": session_id},
                )
            ).first()

            if evaluation is None:
                print("  （没有整场评价记录）")
                continue

            print()
            print("  整场评价：")

            names = (
                "overall_score", "technical_score",
                "project_score", "communication_score",
            )

            for name, value in zip(names, evaluation[:4]):
                print(f"    {name:<22} {value}")

            anchors = (
                "overall_anchor", "technical_anchor",
                "project_anchor", "communication_anchor",
            )

            for name, value in zip(anchors, evaluation[4:8]):
                print(f"    {name:<22} {value}")

            print(f"    anchor_reason: {str(evaluation[8])[:300]}")

    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
