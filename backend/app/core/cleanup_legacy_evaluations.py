"""清理"空评价"记录（能力画像的噪音来源）。

背景：`mindflow` 名下有 3 条 **2026-09-28** 的评价，
`strengths` / `weaknesses` / `suggestions` 全为空、
会话 `target_role` 也为空。它们来自 `strengths` 落库
（D20 / 迁移 `b41f7c9a2e38`）之前 —— 那时评价只写四项分数
与 `feedback`。

为什么必须清：它们会进入**能力画像**的统计。
实测这让 `mindflow` 的画像显示"共 6 次测量、区间 30–90"，
而其中 3 次是没有任何内容的占位数据 —— 用户看到的是
一个被噪音撑大的区间，却无从分辨。

判据刻意保守：**三组内容全为空**才判定为占位数据。
只空一组可能是那一场确实没写出不足（模型输出波动，见 ADR-037），
那种记录是有意义的，不能删。

用法：

    # 只检查
    uv run python -m app.core.cleanup_legacy_evaluations

    # 指定用户
    uv run python -m app.core.cleanup_legacy_evaluations --user mindflow

    # 执行删除
    uv run python -m app.core.cleanup_legacy_evaluations --purge
"""

import argparse
import asyncio
import sys

from sqlalchemy import text

from app.database.session import AsyncSessionLocal

# 三组内容全为空 → 判定为占位数据。
EMPTY_CHECK = """
    COALESCE(json_array_length(strengths), 0) = 0
    AND COALESCE(json_array_length(weaknesses), 0) = 0
    AND COALESCE(json_array_length(suggestions), 0) = 0
"""


async def find_empty_evaluations(
    username: str | None,
) -> list[tuple[int, int, str, str, object]]:
    """返回 [(evaluation_id, session_id, username, feedback, created_at)]。"""

    sql = f"""
        SELECT e.id, e.session_id, u.username, e.feedback, e.created_at
        FROM interview_evaluations e
        JOIN interview_sessions s ON s.id = e.session_id
        JOIN users u ON u.id = s.user_id
        WHERE {EMPTY_CHECK}
    """

    params: dict = {}

    if username:
        sql += " AND u.username = :u"
        params["u"] = username

    sql += " ORDER BY e.created_at"

    async with AsyncSessionLocal() as db:
        rows = list((await db.execute(text(sql), params)).all())

    return [
        (row[0], row[1], row[2], row[3] or "", row[4])
        for row in rows
    ]


async def main() -> int:
    parser = argparse.ArgumentParser(
        description="清理三组内容全空的评价记录",
    )
    parser.add_argument(
        "--user",
        default=None,
        help="只处理该用户（默认全部用户）",
    )
    parser.add_argument(
        "--purge",
        action="store_true",
        help="执行删除（默认只检查）",
    )
    args = parser.parse_args()

    print("=" * 74)
    print("空评价记录检查")
    print("=" * 74)

    rows = await find_empty_evaluations(args.user)

    print(f"  三组内容全空的评价数: {len(rows)}")

    if not rows:
        print()
        print("  ✓ 没有需要清理的记录")
        return 0

    for evaluation_id, session_id, username, feedback, created in rows:
        print(
            f"    eval {evaluation_id} | session {session_id} "
            f"| {username} | {created}"
        )
        if feedback:
            print(f"      feedback: {feedback[:60]}")

    if not args.purge:
        print()
        print("  加 --purge 执行删除")
        return 1

    ids = [row[0] for row in rows]

    async with AsyncSessionLocal() as db:
        await db.execute(
            text("DELETE FROM interview_evaluations WHERE id = ANY(:i)"),
            {"i": ids},
        )
        await db.commit()

    print()
    print(f"  已删除 {len(ids)} 条")

    remaining = await find_empty_evaluations(args.user)
    print(f"  复检剩余: {len(remaining)}")

    if remaining:
        print()
        print("  ✗ 仍有残留")
        return 1

    print()
    print("  ✓ 清理完成")
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
