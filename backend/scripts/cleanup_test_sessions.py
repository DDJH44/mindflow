"""清理空的面试会话（测试残留的主要来源）。

## 它防的是什么

实测：历史页显示 **18 场会话**，其中大量是同名的
"模拟面试 · 已计划 · 0/8 题"。那些是测试脚本留下的空会话
（`test_interview_list` / `test_status_history` / `ui_walkthrough`
各会创建若干）。

它们**不会报错**，但会让真正的用户以为面试历史坏了：
一屏里十几条是空的、同名的、点进去什么都没有。

## 判据：内容量，不是创建者

**空会话（0 题 0 答）** 是这里唯一安全的判据：

- 它不承载任何用户输入（没有回答、没有评价）
- 它不消耗额度（额度在"开始面试并成功生成首题"时才扣）
- 而"先建会话再生成题目"的失败路径**也会留下空会话**，
  所以它不能算真实使用

反过来，只要**有任何一条题目**，就不碰 —— 即使 0 个回答。
用户的答案是最不可替代的数据，而"问了但没答"本身是信息。

## 为什么要年龄门槛

刚创建的会话可能是**正在进行的**：用户点了"开始面试"，
题目还在生成中（实测最慢约 60 秒）。若清理逻辑立刻删掉
0 题 0 答的会话，**会把正在创建的面试删掉**。

因此默认只清理**创建超过 1 小时**的空会话。

用法：
    uv run python -m scripts.cleanup_test_sessions            # 只列出
    uv run python -m scripts.cleanup_test_sessions --purge    # 真删
"""

import argparse
import asyncio
import sys

from sqlalchemy import text

from app.database.session import AsyncSessionLocal

# 默认保留多久以内的空会话（小时）。
#
# 1 小时足够覆盖"正在创建"与"用户刚点开又放弃"两种情况，
# 同时让测试残留（几小时到几天前）都能被清掉。
DEFAULT_MIN_AGE_HOURS = 1.0


async def collect(min_age_hours: float) -> list[dict]:
    """列出满足条件的空会话。"""

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
                            s.created_at,
                            p.name AS project_name
                        FROM interview_sessions s
                        LEFT JOIN projects p ON p.id = s.project_id
                        WHERE NOT EXISTS (
                            SELECT 1 FROM interview_questions q
                            WHERE q.session_id = s.id
                        )
                        AND s.created_at < now() - make_interval(hours => :age)
                        ORDER BY s.id
                        """
                    ),
                    {"age": min_age_hours},
                )
            ).all()
        )

    return [
        {
            "id": row[0],
            "status": row[1],
            "project_id": row[2],
            "created_at": row[3],
            "project_name": row[4],
        }
        for row in rows
    ]


async def purge(sessions: list[dict]) -> int:
    """删除给定的会话及其状态历史。返回删除数量。"""

    if not sessions:
        return 0

    ids = [item["id"] for item in sessions]

    async with AsyncSessionLocal() as db:
        # 顺序：状态历史 → 会话
        # （答案与评价按 question 关联，空会话下没有它们）
        await db.execute(
            text(
                "DELETE FROM interview_status_history "
                "WHERE session_id = ANY(:ids)"
            ),
            {"ids": ids},
        )

        deleted = await db.execute(
            text("DELETE FROM interview_sessions WHERE id = ANY(:ids)"),
            {"ids": ids},
        )

        await db.commit()

    return deleted.rowcount or 0


async def main() -> int:
    sys.path.insert(0, r"D:\yuxi\MindFlow\backend")

    parser = argparse.ArgumentParser(description="清理空的面试会话")
    parser.add_argument(
        "--purge", action="store_true", help="真的删除（默认只列出）"
    )
    parser.add_argument(
        "--min-age-hours",
        type=float,
        default=DEFAULT_MIN_AGE_HOURS,
        help=f"只清理创建超过 N 小时的（默认 {DEFAULT_MIN_AGE_HOURS}）",
    )

    args = parser.parse_args()

    print("=" * 76)
    print("空面试会话清理")
    print("=" * 76)

    sessions = await collect(args.min_age_hours)

    if not sessions:
        print()
        print("  ✓ 没有符合条件的空会话")
        return 0

    print()
    print(
        f"  发现 {len(sessions)} 个空会话"
        f"（创建超过 {args.min_age_hours} 小时，且 0 题 0 答）"
    )
    print()
    print(f"  {'id':>6}  {'状态':<12} {'创建时间':<18} 项目")

    for item in sessions:
        print(
            f"  {item['id']:>6}  {item['status']:<12} "
            f"{item['created_at']:%Y-%m-%d %H:%M}   "
            f"{(item['project_name'] or '(无)')[:20]}"
        )

    if not args.purge:
        print()
        print("  （默认只列出。要真的删除请加 --purge）")
        print()
        print("  ⚠️ 判据是「0 题 0 答」—— 有任何一条题目的会话都不会被删。")
        return 0

    print()
    deleted = await purge(sessions)

    print(f"  已删除 {deleted} 个空会话")
    print()
    print("  提示：这不是数据丢失 —— 空会话不承载任何用户输入，")
    print("        也不消耗额度（额度在成功生成首题时才扣）。")

    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
