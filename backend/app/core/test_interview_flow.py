"""面试流程端到端验证。

覆盖两件在评分评测里**测不到**的事：

1. 状态机在真实业务链路中的推进（ADR-025）
2. 资料依据（evidence_chunk_ids）的捕获与落库（§9.3）

为什么单独成文件、不放进 `app/evaluation/interview/`：
评分评测刻意保持**零数据库依赖**（Milvus / PostgreSQL 挂掉也能跑），
本项目要连真实数据库、且会调用 LLM。
两类问题的运行条件不同，混在一起会互相拖累：
数据库一挂，评分基线也跑不了。

因此本脚本**不参与评分统计**，只验证流程正确性。

依赖：PostgreSQL、Milvus、LLM 均可用。
运行：uv run python -m app.core.test_interview_flow
"""

import asyncio

from sqlalchemy import select, text

from app.database.session import AsyncSessionLocal
from app.core.test_support import grant_quota, reset_quota
from app.models.interview.interview_question import (
    InterviewQuestion,
)
from app.services.interview.evaluation_service import (
    InterviewEvaluationService,
)
from app.services.interview.interview_engine_service import (
    InterviewEngineService,
)
from app.services.interview.interview_state_machine import (
    SessionStatus,
    allowed_transitions,
)
from app.services.interview.interview_turn_service import (
    InterviewTurnService,
)
from app.services.interview_session_service import (
    InterviewSessionService,
)

PROJECT_ID = 3
USER_ID = 3
TARGET_ROLE = "后端工程师（流程验证）"

# 本测试要开始的面试场次
QUOTA = 5

ANSWER = (
    "我负责 MindFlow 的后端开发。用户上传简历后 FastAPI 解析文档，"
    "内容存入 PostgreSQL 并切分成 Chunk，再通过 Embedding 写入 Milvus；"
    "面试时按当前问题做向量检索，取回 Chunk 后按 ID 回表取正文，"
    "最后交给大模型生成问题。"
)

results: list[tuple[str, bool]] = []


def record(label: str, ok: bool) -> None:
    results.append((label, ok))
    print(f"  [{'OK  ' if ok else 'FAIL'}] {label}")


async def main():
    # 自备配额：本测试会 start 面试，消费一次额度。
    # 依赖外部残留状态会让失败原因指向错误的地方（见 D38）。
    await grant_quota(USER_ID, QUOTA)

    async with AsyncSessionLocal() as db:
        service = InterviewSessionService(db)

        # ============================================================
        # 1. 创建会话
        # ============================================================
        print()
        print("=" * 74)
        print("1. 创建会话")
        print("=" * 74)

        session = await service.create_session(
            project_id=PROJECT_ID,
            user_id=USER_ID,
            interview_type="technical",
            target_role=TARGET_ROLE,
        )
        session_id = session.id

        record("初始状态为 draft", session.status == "draft")
        record("target_role 已保存", session.target_role == TARGET_ROLE)
        record("记录会话所有者", session.user_id == USER_ID)

        allowed = sorted(
            item.value for item in allowed_transitions("draft")
        )
        print(f"      draft 允许转移到: {allowed}")

        # ============================================================
        # 2. 状态机非法转移必须被拒
        # ============================================================
        print()
        print("=" * 74)
        print("2. 非法转移必须被拒（且不改库）")
        print("=" * 74)

        for target in ("asking", "completed", "evaluating"):
            try:
                await service.transition_status(
                    interview_id=session_id,
                    target_status=target,
                )
                print(f"      draft → {target:<14} 未拒绝（异常）")
                record(f"draft → {target} 应被拒绝", False)
            except ValueError:
                print(f"      draft → {target:<14} 已拒绝")
                record(f"draft → {target} 被拒绝", True)

        # 复核库里的状态没被非法转移改坏
        fresh = await service.get_session(session_id)
        record("非法转移后状态仍为 draft", fresh.status == "draft")

        # ============================================================
        # 3. 合法推进到 planned
        # ============================================================
        print()
        print("=" * 74)
        print("3. draft → preparing_context → planned")
        print("=" * 74)

        await service.transition_status(
            interview_id=session_id,
            target_status=SessionStatus.PREPARING_CONTEXT,
        )
        planned = await service.transition_status(
            interview_id=session_id,
            target_status=SessionStatus.PLANNED,
        )
        record("已到达 planned", planned.status == "planned")

        # ============================================================
        # 4. 生成问题：planned → asking，并捕获资料依据
        # ============================================================
        print()
        print("=" * 74)
        print("4. 生成问题（planned → asking）与资料依据")
        print("=" * 74)

        engine = InterviewEngineService(db)
        question = await engine.generate_and_save_question(
            session_id=session_id,
            project_id=PROJECT_ID,
            query="考察候选人的后端与 RAG 实践经验",
            question_type="technical",
            # 不传 question_index：索引由服务端按会话的
            # current_question_index 自动推导
        )

        row = (
            await db.execute(
                select(InterviewQuestion).where(
                    InterviewQuestion.id == question.id
                )
            )
        ).scalar_one()

        after_question = await service.get_session(session_id)
        record("生成问题后状态为 asking", after_question.status == "asking")

        evidence = list(row.evidence_chunk_ids or [])
        print(f"      问题: {row.question[:56]}...")
        print(f"      evidence_chunk_ids = {evidence}")
        print(f"      is_general = {row.is_general}")

        # 资料依据与"是否通用题"必须自洽：
        # 有依据就不能标为通用题，反之亦然（ADR-024）
        evidence_consistent = (
            row.is_general is False and bool(evidence)
        ) or (row.is_general is True and not evidence)
        record("证据与 is_general 自洽", evidence_consistent)

        # ============================================================
        # 5. 答题：asking → evaluating → asking
        # ============================================================
        print()
        print("=" * 74)
        print("5. 答题（asking → evaluating → asking）")
        print("=" * 74)

        turn = await InterviewTurnService(db).process_answer(
            question_id=row.id,
            answer=ANSWER,
        )

        after_turn = await service.get_session(session_id)
        record("答题后回到 asking", after_turn.status == "asking")
        record(
            "追问已生成",
            turn["follow_up_question"] is not None,
        )

        follow_up = turn["follow_up_question"]
        print(f"      追问: {follow_up.question[:56]}...")
        print(
            f"      追问继承的依据 = "
            f"{list(follow_up.evidence_chunk_ids or [])}"
        )
        record(
            "追问继承原问题的资料依据",
            list(follow_up.evidence_chunk_ids or []) == evidence,
        )

        # ============================================================
        # 6. 整场评价：evaluating → summarizing → completed
        # ============================================================
        print()
        print("=" * 74)
        print("6. 整场评价（→ summarizing → completed）")
        print("=" * 74)

        # 评价要求会话处于 evaluating
        await service.transition_status(
            interview_id=session_id,
            target_status=SessionStatus.WAITING_FOR_ANSWER,
        )
        await service.transition_status(
            interview_id=session_id,
            target_status=SessionStatus.EVALUATING,
        )

        evaluation_result = (
            await InterviewEvaluationService(db).evaluate_session(
                session_id=session_id
            )
        )

        saved = evaluation_result["saved_evaluation"]
        final = await service.get_session(session_id)

        print(
            f"      分数 = {saved.overall_score}/"
            f"{saved.technical_score}/"
            f"{saved.project_score}/"
            f"{saved.communication_score}"
        )
        print(f"      strengths={len(saved.strengths or [])} "
              f"weaknesses={len(saved.weaknesses or [])} "
              f"suggestions={len(saved.suggestions or [])}")

        record("评价完成后状态为 completed", final.status == "completed")

        # 断言"结构化字段已落库且内容可用"，而不是"三组都非空"。
        #
        # 为什么改：实测 5 次里有 1 次 `weaknesses=0` —— 模型这一轮
        # 确实没给出"不足"。这是**模型输出波动**（ADR-037 已定：
        # 不再调提示词），不是解析丢内容。
        #
        # 判据：解析是确定性的 —— 若它把字段丢了，该字段会**每次**都空。
        # 偶发空只能是模型没返回。而"三组都非空"不是产品要求：
        # 一份全是优点的回答本来就可能没有"不足"可写。
        #
        # 真正必须成立的是：字段是列表类型、能落库、能读回，
        # 且至少有一组内容 —— 否则说明解析或落库真的坏了。
        strengths = list(saved.strengths or [])
        weaknesses = list(saved.weaknesses or [])
        suggestions = list(saved.suggestions or [])

        record(
            "结构化评价内容已落库",
            isinstance(strengths, list)
            and isinstance(weaknesses, list)
            and isinstance(suggestions, list)
            and len(strengths) + len(weaknesses) + len(suggestions) > 0,
        )
        record(
            "结构化字段非空率合理（至少含建议或优势）",
            bool(strengths) or bool(suggestions),
        )
        record("评分明细已落库", bool(saved.scoring_details))

        # ============================================================
        # 7. completed 是终态
        # ============================================================
        print()
        print("=" * 74)
        print("7. completed 是真终态（ADR-026）")
        print("=" * 74)

        final_allowed = sorted(
            item.value for item in allowed_transitions("completed")
        )
        print(f"      completed 允许转移到: {final_allowed}")
        record("completed 无任何后继状态", final_allowed == [])

        try:
            await service.transition_status(
                interview_id=session_id,
                target_status=SessionStatus.ASKING,
            )
            record("completed → asking 应被拒绝", False)
        except ValueError:
            record("completed → asking 被拒绝", True)

        # ============================================================
        # 8. 错误路径不应留下脏状态
        # ============================================================
        print()
        print("=" * 74)
        print("8. 错误路径不留脏状态")
        print("=" * 74)

        # 场景：completed 会话不允许再答题。
        # 这验证的是"先校验状态、再干活"——
        # 若实现是"先写回答再校验"，这里就会多出一条脏回答。
        answers_before = (
            await db.execute(
                text(
                    "SELECT COUNT(*) FROM interview_answers "
                    "WHERE question_id = :qid"
                ),
                {"qid": row.id},
            )
        ).scalar()

        try:
            await InterviewTurnService(db).process_answer(
                question_id=row.id,
                answer="这条回答不应被写入",
            )
            record("completed 会话答题应被拒绝", False)
        except ValueError:
            record("completed 会话答题被拒绝", True)

        answers_after = (
            await db.execute(
                text(
                    "SELECT COUNT(*) FROM interview_answers "
                    "WHERE question_id = :qid"
                ),
                {"qid": row.id},
            )
        ).scalar()

        record(
            "被拒的答题没有写入任何回答（先校验后执行）",
            answers_before == answers_after,
        )
        print(
            f"      拒绝前回答数={answers_before} "
            f"拒绝后={answers_after}"
        )

        # 场景：对一个不存在的会话做转移，应返回 None 而不是报错
        missing = await service.transition_status(
            interview_id=999999,
            target_status=SessionStatus.PLANNED,
        )
        record("对不存在的会话转移返回 None", missing is None)

        # ============================================================
        # 9. 清理
        # ============================================================
        print()
        print("=" * 74)
        await db.execute(
            text("DELETE FROM interview_sessions WHERE id = :sid"),
            {"sid": session_id},
        )
        await db.commit()
        print(f"已清理验证会话 {session_id}")

        # ============================================================
        # 汇总
        # ============================================================
        print()
        print("=" * 74)
        passed = sum(1 for _, ok in results if ok)
        failed = [
            label for label, ok in results if not ok
        ]

        print(f"通过 {passed} / {len(results)}")

    # 恢复默认配额，避免影响后续脚本
    await reset_quota(USER_ID)

    if failed:
        print("失败项:")
        for label in failed:
            print("  -", label)
        raise SystemExit(1)

    print("全部通过")


asyncio.run(main())
