"""一次性清理走查/测试留下的已完成会话。

保留：
- 会话 1、2：历史遗留数据
- 其它用户（真实用户）的会话：一律不碰

清理：`mindflow` 名下、**不在保留名单里**的会话。

用法：uv run python -m app.core.cleanup_demo_sessions --keep 1,2
"""

import argparse
import asyncio
import sys

from sqlalchemy import text

from app.database.session import AsyncSessionLocal

ACCOUNT = "mindflow"


async def main() -> int:
    parser = argparse.ArgumentParser(
        description="清理测试留下的面试会话",
    )
    parser.add_argument(
        "--keep",
        default="1,2",
        help="要保留的会话 id，逗号分隔（默认 1,2）",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="只列出将被删除的会话",
    )
    args = parser.parse_args()

    keep = [
        int(item)
        for item in args.keep.split(",")
        if item.strip().isdigit()
    ]

    async with AsyncSessionLocal() as db:
        user_id = (
            await db.execute(
                text("SELECT id FROM users WHERE username = :u"),
                {"u": ACCOUNT},
            )
        ).scalar_one_or_none()

        if user_id is None:
            print(f"  找不到用户 {ACCOUNT}")
            return 1

        rows = list(
            (
                await db.execute(
                    text(
                        "SELECT id, status FROM interview_sessions "
                        "WHERE user_id = :u AND id <> ALL(:keep) "
                        "ORDER BY id"
                    ),
                    {"u": user_id, "keep": keep},
                )
            ).all()
        )

        print(f"  保留: {keep}")
        print(f"  待清理 {len(rows)} 场:")
        for session_id, status in rows:
            print(f"    id={session_id} status={status}")

        if args.dry_run:
            print("  （dry-run，未执行删除）")
            return 0

        ids = [row[0] for row in rows]

        if not ids:
            print("  无需清理")
            return 0

        await db.execute(
            text(
                "DELETE FROM interview_answers WHERE question_id IN "
                "(SELECT id FROM interview_questions "
                " WHERE session_id = ANY(:s))"
            ),
            {"s": ids},
        )
        await db.execute(
            text(
                "DELETE FROM interview_questions "
                "WHERE session_id = ANY(:s)"
            ),
            {"s": ids},
        )
        await db.execute(
            text(
                "DELETE FROM interview_evaluations "
                "WHERE session_id = ANY(:s)"
            ),
            {"s": ids},
        )
        await db.execute(
            text(
                "DELETE FROM interview_status_history "
                "WHERE session_id = ANY(:s)"
            ),
            {"s": ids},
        )
        await db.execute(
            text("DELETE FROM interview_sessions WHERE id = ANY(:s)"),
            {"s": ids},
        )
        await db.commit()

        remaining = list(
            (
                await db.execute(
                    text(
                        "SELECT id, status FROM interview_sessions "
                        "ORDER BY id"
                    )
                )
            ).all()
        )

    print(f"  已清理 {len(ids)} 场")
    print(f"  剩余会话: {remaining}")

    # 会话删了，向量可能留下孤儿
    try:
        from app.core.cleanup_test_users import purge_orphan_vectors

        purged = await purge_orphan_vectors()
        if purged:
            print(f"  已清理孤儿向量 {purged} 个")
    except Exception:  # noqa: BLE001
        pass

    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
