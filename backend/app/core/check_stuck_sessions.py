"""检查卡住的面试会话。

## 它解决什么问题

"结束并生成报告"会先把会话推进到 `evaluating`，再调模型做整场评价。
若那一步失败（上游超时、限流），会话就**停在 evaluating**。

设计上这是**可重试**的（`finish_interview` 对 evaluating 直接放行，
见 ADR-025）。但在此之前：

- 界面上没有"重试"入口 —— 反而显示题目与回答框，
  用户提交后拿到原始状态机报错（D76）
- **没有任何机制发现"这个会话卡了很久"**

实测库里有 2 个会话在 evaluating 卡了 **5 天**。
它们不会报错、不会告警、不会自己恢复 —— 只会一直躺在历史页里，
让用户以为"这个功能坏了"。

本工具把这件事变成**可见**的。

## 为什么只关心 evaluating

其它非终态（`planned` / `asking` / `paused`）是**用户正常的中间状态**：

- `asking` 超过一周没动，可能是用户自己没继续 —— 那是他的选择
- `paused` 同理，而且用户随时可以"继续面试"

而 `evaluating` 不同：它表示"**系统该做完一件事但没做完**"。
用户在这个状态下什么也做不了（作答会被拒），
所以它是唯一的"真正的卡住"。

区分这两类很重要：把所有久未更新的会话都当问题，
会让这个检查充满噪音，而噪音会让人不再看它。

用法：
    # 只列出（默认只看超过 30 分钟的）
    uv run python -m app.core.check_stuck_sessions

    # 自定义阈值
    uv run python -m app.core.check_stuck_sessions --min-minutes 5

    # 真的重试（会调用模型、耗时较长）
    uv run python -m app.core.check_stuck_sessions --retry
"""

import argparse
import asyncio
import sys
from datetime import timedelta

from sqlalchemy import text

from app.core.time_convention import utc_now
from app.database.session import AsyncSessionLocal

# 默认阈值：30 分钟。
#
# 依据：最慢的路径是"整场评价"，实测 30-90 秒，冷启动更久。
# 30 分钟远大于任何正常耗时，因此超过它的 evaluating 一定是失败了，
# 而不是"还在跑"。
DEFAULT_MIN_MINUTES = 30


async def collect(min_minutes: int) -> list[dict]:
    """列出卡在 evaluating 的会话。"""

    threshold = utc_now() - timedelta(minutes=min_minutes)

    async with AsyncSessionLocal() as db:
        rows = list(
            (
                await db.execute(
                    text(
                        """
                        SELECT
                            s.id,
                            s.user_id,
                            u.username,
                            s.project_id,
                            s.questions_asked,
                            s.max_questions,
                            s.updated_at,
                            (SELECT count(*) FROM interview_questions q
                             WHERE q.session_id = s.id) AS qs,
                            (SELECT count(*) FROM interview_answers a
                             JOIN interview_questions q2
                               ON q2.id = a.question_id
                             WHERE q2.session_id = s.id) AS ans
                        FROM interview_sessions s
                        JOIN users u ON u.id = s.user_id
                        WHERE s.status = 'evaluating'
                          AND s.updated_at < :threshold
                        ORDER BY s.updated_at
                        """
                    ),
                    {"threshold": threshold},
                )
            ).all()
        )

    return [
        {
            "id": row[0],
            "user_id": row[1],
            "username": row[2],
            "project_id": row[3],
            "questions_asked": row[4],
            "max_questions": row[5],
            "updated_at": row[6],
            "questions": row[7],
            "answers": row[8],
        }
        for row in rows
    ]


async def summarize_other_states(min_minutes: int) -> dict[str, int]:
    """统计其它非终态的数量，用于说明"它们不是问题"。

    刻意只计数不列出：`planned` / `asking` / `paused` 是用户
    正常的中间状态，列出来只会制造噪音。
    """

    threshold = utc_now() - timedelta(minutes=min_minutes)

    async with AsyncSessionLocal() as db:
        rows = list(
            (
                await db.execute(
                    text(
                        """
                        SELECT status, count(*)
                        FROM interview_sessions
                        WHERE status NOT IN ('completed', 'cancelled', 'failed')
                          AND status <> 'evaluating'
                          AND updated_at < :threshold
                        GROUP BY status
                        """
                    ),
                    {"threshold": threshold},
                )
            ).all()
        )

    return {row[0]: row[1] for row in rows}


async def retry(session: dict) -> str:
    """尝试重新评价一个卡住的会话。返回结果描述。"""

    from app.services.interview.interview_flow_service import (
        InterviewFlowService,
    )

    async with AsyncSessionLocal() as db:
        try:
            result = await InterviewFlowService(
                db
            ).finish_interview(session_id=session["id"])

            if result is None:
                return "会话不存在"

            evaluation = result["evaluation"]

            return (
                f"成功 → {result['session_status']}"
                f"（综合 {evaluation.overall_score}）"
            )

        except Exception as exc:  # noqa: BLE001
            return f"失败：{type(exc).__name__}: {str(exc)[:80]}"


async def main() -> int:
    sys.path.insert(0, r"D:\yuxi\MindFlow\backend")

    parser = argparse.ArgumentParser(
        description="检查卡在 evaluating 的面试会话"
    )
    parser.add_argument(
        "--min-minutes",
        type=int,
        default=DEFAULT_MIN_MINUTES,
        help=f"超过多少分钟算卡住（默认 {DEFAULT_MIN_MINUTES}）",
    )
    parser.add_argument(
        "--retry",
        action="store_true",
        help="真的重试评价（会调用模型，耗时较长）",
    )

    args = parser.parse_args()

    print("=" * 78)
    print("卡住会话检查")
    print("=" * 78)

    stuck = await collect(args.min_minutes)

    if not stuck:
        print()
        print(f"  ✓ 没有卡在 evaluating 的会话（阈值 {args.min_minutes} 分钟）")

        others = await summarize_other_states(args.min_minutes)

        if others:
            print()
            print("  说明：以下状态**不是问题**，是用户正常的中间状态：")

            for status, count in sorted(others.items()):
                print(f"    {status:<12} {count} 个")

        return 0

    print()
    print(
        f"  ✗ 发现 {len(stuck)} 个卡在 evaluating 的会话"
        f"（超过 {args.min_minutes} 分钟）："
    )
    print()
    print(
        f"  {'id':>6}  {'用户':<14} {'题/答':<10} "
        f"{'最后更新':<18} 项目"
    )

    for item in stuck:
        print(
            f"  {item['id']:>6}  {item['username'][:14]:<14} "
            f"{item['answers']}/{item['questions']:<8} "
            f"{item['updated_at']:%Y-%m-%d %H:%M}   "
            f"proj={item['project_id']}"
        )

    print()
    print("  这意味着什么：整场评价那一步失败了，而会话停在了")
    print("  `evaluating`。用户在这个状态下无法继续作答，也拿不到报告。")
    print("  数据没有丢（问答都在），只是评价没做完。")

    if not args.retry:
        print()
        print("  两种恢复方式：")
        print("    ① 让用户在面试页点「重试生成报告」（界面已支持）")
        print("    ② 本机执行：uv run python -m app.core.check_stuck_sessions --retry")
        print()
        print("  ⚠️ 重试会调用模型（每场约 30-90 秒）。默认不动数据。")
        return 1

    print()
    print("  开始重试…")

    failed = 0

    for item in stuck:
        print(f"    id={item['id']} …", end=" ", flush=True)

        outcome = await retry(item)
        print(outcome)

        if not outcome.startswith("成功"):
            failed += 1

    print()
    print(f"  重试完成：{len(stuck) - failed} 成功，{failed} 失败")

    if failed:
        print("  失败的多半是上游模型不可用 —— 稍后重跑本命令即可。")

    return 0 if failed == 0 else 1


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
