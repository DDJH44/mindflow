"""清理测试遗留的面试会话与文档测试数据。

保留用户 `mindflow` 的真实数据（会话 1、2 与项目 1–3）。

用法：uv run python -m app.core.cleanup_test_data
"""

import asyncio
import sys

from sqlalchemy import text

from app.database.session import AsyncSessionLocal

# 保留的会话（真实数据）
KEEP_SESSION_IDS = (1, 2)


async def main() -> int:
    async with AsyncSessionLocal() as db:
        tables = set(
            (
                await db.execute(
                    text(
                        "SELECT tablename FROM pg_tables "
                        "WHERE schemaname = 'public'"
                    )
                )
            ).scalars().all()
        )

        session_ids = list(
            (
                await db.execute(
                    text(
                        "SELECT id FROM interview_sessions "
                        "WHERE id <> ALL(:keep) ORDER BY id"
                    ),
                    {"keep": list(KEEP_SESSION_IDS)},
                )
            ).scalars().all()
        )

        print(f"  待清理会话: {session_ids}")

        if session_ids:
            if "interview_status_history" in tables:
                await db.execute(
                    text(
                        "DELETE FROM interview_status_history "
                        "WHERE session_id = ANY(:s)"
                    ),
                    {"s": session_ids},
                )

            question_ids = []
            if "interview_questions" in tables:
                question_ids = list(
                    (
                        await db.execute(
                            text(
                                "SELECT id FROM interview_questions "
                                "WHERE session_id = ANY(:s)"
                            ),
                            {"s": session_ids},
                        )
                    ).scalars().all()
                )

            # 答案表名不写死：从实际表名里找含 answer 的表
            if question_ids:
                for table in sorted(tables):
                    if "answer" in table:
                        await db.execute(
                            text(
                                f"DELETE FROM {table} "
                                "WHERE question_id = ANY(:q)"
                            ),
                            {"q": question_ids},
                        )

                await db.execute(
                    text(
                        "DELETE FROM interview_questions "
                        "WHERE session_id = ANY(:s)"
                    ),
                    {"s": session_ids},
                )

            for table in sorted(tables):
                if "evaluation" in table:
                    await db.execute(
                        text(
                            f"DELETE FROM {table} "
                            "WHERE session_id = ANY(:s)"
                        ),
                        {"s": session_ids},
                    )

            await db.execute(
                text(
                    "DELETE FROM interview_sessions "
                    "WHERE id = ANY(:s)"
                ),
                {"s": session_ids},
            )

            await db.commit()

        sessions = list(
            (
                await db.execute(
                    text(
                        "SELECT id, status FROM interview_sessions "
                        "ORDER BY id"
                    )
                )
            ).all()
        )
        documents = list(
            (
                await db.execute(
                    text("SELECT id, status FROM documents ORDER BY id")
                )
            ).all()
        )

        print(f"  最终会话: {sessions}")
        print(f"  最终文档: {documents}")

    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
