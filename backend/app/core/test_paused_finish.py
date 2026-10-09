"""验证暂停会话能正常结束（D56）。

用户报告：回答一题后暂停，再点"结束并生成报告"得到
    非法状态转移：paused → waiting_for_answer；
    允许的目标状态为 ['cancelled']（paused 会话缺少 resume_status，只能取消）

根因：`finish_interview` 假设会话处于活跃态，无条件推进
`→ waiting_for_answer`；而 `paused` 的唯一合法后继是 `cancelled`。

本脚本覆盖四条路径：
1. paused（有 resume_status=asking）+ 有未答题 → 能结束
2. paused（resume_status 为 NULL）+ 有未答题 → 靠推断恢复后结束
3. paused（resume_status 为 NULL）+ 有已答题、无未答题 → 推断为 waiting_for_answer
4. paused（无任何题目）→ 给出**可理解**的报错，而不是状态机术语

用法：uv run python -m app.core.test_paused_finish
"""

import asyncio
import sys

from sqlalchemy import text

from app.database.session import AsyncSessionLocal

PROJECT_ID = 3
USER_ID = 3

results: list[tuple[str, bool]] = []


def record(label: str, ok: bool, extra: str = "") -> None:
    results.append((label, ok))
    suffix = f"  {extra}" if extra else ""
    print(f"  [{'OK  ' if ok else 'FAIL'}] {label}{suffix}")


async def make_session(
    rows: list[tuple[str, str | None]],
    resume_status: str | None,
) -> int:
    """造一个 paused 会话，并写入给定的题目/答案。

    rows: [(question_text, answer_or_None), ...]
    """

    async with AsyncSessionLocal() as db:
        session_id = (
            await db.execute(
                text(
                    "INSERT INTO interview_sessions "
                    "(project_id, user_id, status, interview_type, "
                    " current_question_index, max_questions, "
                    " questions_asked, resume_status, created_at, "
                    " updated_at) "
                    "VALUES (:p, :u, 'paused', 'technical', :idx, 8, "
                    " :asked, :resume, now(), now()) "
                    "RETURNING id"
                ),
                {
                    "p": PROJECT_ID,
                    "u": USER_ID,
                    "idx": len(rows),
                    "asked": len(rows),
                    "resume": resume_status,
                },
            )
        ).scalar_one()

        for index, (question, answer) in enumerate(rows):
            question_id = (
                await db.execute(
                    text(
                        "INSERT INTO interview_questions "
                        "(session_id, question, question_type, "
                        " question_index, context, evidence_chunk_ids, "
                        " is_general, created_at) "
                        "VALUES (:s, :q, 'technical', :i, NULL, "
                        " '{}', false, now()) "
                        "RETURNING id"
                    ),
                    {
                        "s": session_id,
                        "q": question,
                        "i": index,
                    },
                )
            ).scalar_one()

            if answer is not None:
                await db.execute(
                    text(
                        "INSERT INTO interview_answers "
                        "(question_id, answer, created_at) "
                        "VALUES (:q, :a, now())"
                    ),
                    {"q": question_id, "a": answer},
                )

        await db.commit()

    return session_id


async def cleanup(session_id: int) -> None:
    async with AsyncSessionLocal() as db:
        await db.execute(
            text(
                "DELETE FROM interview_evaluations "
                "WHERE session_id = :s"
            ),
            {"s": session_id},
        )
        await db.execute(
            text(
                "DELETE FROM interview_answers WHERE question_id IN "
                "(SELECT id FROM interview_questions "
                " WHERE session_id = :s)"
            ),
            {"s": session_id},
        )
        await db.execute(
            text(
                "DELETE FROM interview_questions WHERE session_id = :s"
            ),
            {"s": session_id},
        )
        await db.execute(
            text(
                "DELETE FROM interview_status_history "
                "WHERE session_id = :s"
            ),
            {"s": session_id},
        )
        await db.execute(
            text("DELETE FROM interview_sessions WHERE id = :s"),
            {"s": session_id},
        )
        await db.commit()


async def session_status(session_id: int) -> str:
    async with AsyncSessionLocal() as db:
        return (
            await db.execute(
                text(
                    "SELECT status FROM interview_sessions "
                    "WHERE id = :s"
                ),
                {"s": session_id},
            )
        ).scalar_one()


async def main() -> int:
    from app.services.interview.interview_flow_service import (
        InterviewFlowService,
    )

    cases = [
        {
            "name": "1. paused(resume_status=asking) + 有未答题",
            "rows": [("第一题", None)],
            "resume": "asking",
            "expect_finish": True,
        },
        {
            "name": "2. paused(resume_status=NULL) + 有未答题（靠推断）",
            "rows": [("第一题", None)],
            "resume": None,
            "expect_finish": True,
        },
        {
            "name": "3. paused(resume_status=NULL) + 已答过、无未答题",
            "rows": [("第一题", "我的回答"), ("第二题", None)],
            "resume": None,
            "expect_finish": True,
        },
        {
            "name": "4. paused 但没有任何题目",
            "rows": [],
            "resume": None,
            "expect_finish": False,
        },
    ]

    for case in cases:
        print()
        print("=" * 74)
        print(case["name"])
        print("=" * 74)

        session_id = await make_session(
            rows=case["rows"], resume_status=case["resume"]
        )
        print(f"  会话 {session_id} 已就绪")

        try:
            try:
                async with AsyncSessionLocal() as db:
                    result = await InterviewFlowService(
                        db
                    ).finish_interview(session_id=session_id)

                status = await session_status(session_id)
                finished = result is not None

                record(
                    "能结束并生成评价",
                    finished and case["expect_finish"],
                    f"evaluation={finished}, status={status}",
                )

                if case["expect_finish"]:
                    record(
                        "会话进入终态 completed",
                        status == "completed",
                        f"status={status}",
                    )

            except Exception as exc:  # noqa: BLE001
                message = str(exc)

                if case["expect_finish"]:
                    record("能结束并生成评价", False, f"{type(exc).__name__}: {message[:70]}")
                else:
                    # 这条本就该失败，但**错误信息必须可理解** ——
                    # 不能把状态机术语抛给用户
                    record(
                        "给出可理解的报错",
                        "无法确定恢复到哪个状态" in message
                        and "只能取消" in message,
                        message[:80],
                    )

        finally:
            try:
                await cleanup(session_id)
            except Exception as exc:  # noqa: BLE001
                print(
                    f"      !! 清理会话 {session_id} 失败: "
                    f"{type(exc).__name__}: {exc}"
                )

    # ================================================
    # 5. 重复结束：终态应返回 409，且理由可读
    # ================================================
    print()
    print("=" * 74)
    print("5. 对已完成会话重复结束")
    print("=" * 74)

    session_id = await make_session(
        rows=[("第一题", "我的回答")], resume_status=None
    )

    try:
        async with AsyncSessionLocal() as db:
            await InterviewFlowService(db).finish_interview(
                session_id=session_id
            )

        status = await session_status(session_id)
        record("首次结束成功", status == "completed", f"status={status}")

        # 第二次结束：应被拒，且**理由要说"已经结束过"**，
        # 而不是状态机术语或"还没有进行任何一轮问答"。
        from fastapi import HTTPException

        try:
            async with AsyncSessionLocal() as db:
                await InterviewFlowService(db).finish_interview(
                    session_id=session_id
                )
            record("重复结束被拒绝", False, "竟然成功")
        except HTTPException as exc:
            detail = str(exc.detail)
            record(
                "重复结束返回 409",
                exc.status_code == 409,
                f"status={exc.status_code}",
            )
            record(
                "理由说明「已经结束过」",
                "已经结束过" in detail,
                detail[:70],
            )
        except Exception as exc:  # noqa: BLE001
            record(
                "重复结束返回 409",
                False,
                f"{type(exc).__name__}: {str(exc)[:60]}",
            )

    finally:
        await cleanup(session_id)

    print()
    print("=" * 74)
    passed = sum(1 for _, ok in results if ok)
    failed = [label for label, ok in results if not ok]
    print(f"通过 {passed} / {len(results)}")
    if failed:
        print("失败项:")
        for label in failed:
            print("  -", label)
        return 1

    print("全部通过")
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
