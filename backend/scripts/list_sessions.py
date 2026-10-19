"""列出所有面试会话及其内容量，用于识别测试污染。

实测背景：历史页显示 **18 场会话**，其中大量是
"模拟面试 · 已计划 · 0/8 题" —— 那些是**测试脚本留下的空会话**
（`test_interview_list` / `test_status_history` / `ui_walkthrough`
各会创建若干）。

它们不会报错，但会让**真正的用户以为面试历史坏了**：
一屏 18 条里有 14 条是空的、同名的、点进去什么都没有。

因此需要能一眼分辨"哪些是真实使用产生的"。
判据不是创建者，而是**内容量**（题数 / 答数）。

用法：uv run python -m scripts.list_sessions
"""

import asyncio
import sys
from collections import Counter

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
                        SELECT
                            s.id,
                            s.status,
                            s.project_id,
                            s.user_id,
                            s.created_at,
                            p.name AS project_name,
                            (SELECT count(*) FROM interview_questions q
                             WHERE q.session_id = s.id) AS qs,
                            (SELECT count(*) FROM interview_answers a
                             JOIN interview_questions q2
                               ON q2.id = a.question_id
                             WHERE q2.session_id = s.id) AS ans
                        FROM interview_sessions s
                        LEFT JOIN projects p ON p.id = s.project_id
                        ORDER BY s.id
                        """
                    )
                )
            ).all()
        )

        print()
        print(f"  会话共 {len(rows)} 个")
        print(f"  状态分布: {dict(Counter(r[1] for r in rows))}")

        # 按内容量分类 —— 这才是"是不是测试垃圾"的判据
        empty = [r for r in rows if r[6] == 0 and r[7] == 0]
        with_content = [r for r in rows if r[6] > 0 or r[7] > 0]

        print(
            f"  空会话（0 题 0 答）: {len(empty)} 个"
            f"  ｜  有内容: {len(with_content)} 个"
        )

        print()
        print("  --- 有内容的会话（可能是真实使用）---")
        for row in with_content:
            print(
                f"    id={row[0]:<5} {row[1]:<10} proj={row[2]} "
                f"题={row[6]} 答={row[7]} "
                f"[{str(row[5])[:18]}] {row[4]:%m-%d %H:%M}"
            )

        print()
        print("  --- 空会话（测试残留的典型特征）---")
        by_project = Counter((r[2], str(r[5])[:20]) for r in empty)

        for (project_id, name), count in by_project.items():
            print(f"    proj={project_id} [{name}] : {count} 个空会话")

        print()
        print("  清理：uv run python -m scripts.cleanup_test_sessions")

    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
