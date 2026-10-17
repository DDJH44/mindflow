"""造一条 paused 会话，用于截图检查暂停提示（§10.6）。

## 为什么要专门造数据

`paused` 提示的三种分支（有未答题 / 还没出题 / 答案都已提交）
**只在暂停态出现**，而正常流程要走完整面试才会到那里。
为了截图检查它，需要一个可控的 paused 会话。

## 它做什么

在指定项目下建一条会话，构造"已答 1 题、第 2 题已问出未作答"，
然后置为 `paused`。跑完会打印会话 id，供截图脚本使用。

**它自己不清理** —— 因为清理逻辑应当由
`cleanup_test_sessions.py` 统一负责（空会话 + 年龄门槛）。
这里刻意不重复实现第二套清理。

用法：uv run python -m app.core.seed_paused_session
"""

import asyncio
import sys

from sqlalchemy import text

from app.database.session import AsyncSessionLocal

ACCOUNT = "mindflow"
PROJECT_ID = 3


async def main() -> int:
    sys.path.insert(0, r"D:\yuxi\MindFlow\backend")

    async with AsyncSessionLocal() as db:
        user_id = (
            await db.execute(
                text("SELECT id FROM users WHERE username=:u"),
                {"u": ACCOUNT},
            )
        ).scalar_one()

        # 会话：已问 2 题、1 题已答、状态 paused
        session_id = (
            await db.execute(
                text(
                    """
                    INSERT INTO interview_sessions (
                        project_id, user_id, status, interview_type,
                        target_role, current_question_index,
                        max_questions, questions_asked,
                        resume_status, pause_reason, paused_at,
                        created_at, updated_at
                    )
                    VALUES (
                        :p, :u, 'paused', 'mixed', '后端工程师', 1,
                        8, 2, 'asking', '用户主动暂停', now(),
                        now(), now()
                    )
                    RETURNING id
                    """
                ),
                {"p": PROJECT_ID, "u": user_id},
            )
        ).scalar_one()

        # 第 1 题：已作答
        question_one = (
            await db.execute(
                text(
                    """
                    INSERT INTO interview_questions (
                        session_id, question, question_type,
                        question_index, is_general, created_at
                    )
                    VALUES (
                        :s, '请介绍你在 PostgreSQL 上做过的一次索引优化。',
                        'project', 0, false, now()
                    )
                    RETURNING id
                    """
                ),
                {"s": session_id},
            )
        ).scalar_one()

        await db.execute(
            text(
                """
                INSERT INTO interview_answers (
                    question_id, answer, created_at
                )
                VALUES (:q, :a, now())
                """
            ),
            {
                "q": question_one,
                "a": (
                    "我们把一个 200 万行的订单表按 created_at 做了分区，"
                    "并给 (status, created_at) 建了复合索引。"
                ),
            },
        )

        # 第 2 题：已问出、**未作答** —— 这是"继续后回到那道题"的分支
        await db.execute(
            text(
                """
                INSERT INTO interview_questions (
                    session_id, question, question_type,
                    question_index, is_general, created_at
                )
                VALUES (
                    :s,
                    '那次分区的边界是怎么定的？如果按天分区会有什么问题？',
                    'project', 1, false, now()
                )
                """
            ),
            {"s": session_id},
        )

        await db.commit()

        print()
        print(f"  已创建 paused 会话 id={session_id}")
        print(f"  项目 {PROJECT_ID}、用户 {ACCOUNT}、已答 1 / 8 题")
        print("  第 2 题已问出、未作答 —— 应显示"
              "'第 2 题已问出但还没作答'")
        print()
        print(f"  截图后可用 cleanup_test_sessions.py 清理"
              f"（它是 paused，不会被'空会话'判据命中）")
        print(f"  如需手动删除：DELETE FROM interview_sessions "
              f"WHERE id = {session_id};")

    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
