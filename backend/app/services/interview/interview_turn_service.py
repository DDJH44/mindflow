from sqlalchemy.ext.asyncio import AsyncSession

from app.repositories.interview_session_repository import (
    InterviewSessionRepository,
)
from app.services.interview.answer_analyzer import (
    InterviewAnswerAnalyzer,
    online_analysis_samples,
)
from app.services.interview.follow_up_generator import (
    InterviewFollowUpGenerator,
)
from app.services.interview.interview_state_machine import (
    SessionStatus,
    TransitionTrigger,
    assert_transition,
)
from app.services.interview.interview_termination import (
    TerminationReason,
    decide_continue,
)
from app.services.interview_answer_service import (
    InterviewAnswerService,
)
from app.services.interview_question_service import (
    InterviewQuestionService,
)
from app.services.interview_session_service import (
    InterviewSessionService,
)
from app.services.usage.usage_quota import UsageMetric
from app.services.usage.usage_service import UsageService


class InterviewTurnService:
    """处理一次完整的面试回答流程。

    状态推进遵循 ADR-025：**只在外层调用成功后才推进状态**。

    具体做法是先在内存中标状态、最后一次性提交，而不是每步都提交。
    这样任何中途失败（LLM 报错、超时、JSON 解析失败）都不会
    把库里的状态改坏 —— 会话留在进入本轮之前的状态，可直接重试。

    为什么不能"先声明意图再执行"：
    那看起来更符合状态机的直觉，但会让会话卡在 evaluating：
    LLM 失败后状态已经提交为 evaluating，而 evaluating 的语义是
    "正在评价"，用户既不能继续答题，也无法解释为什么。

    本服务还负责**终止判定**：回答完之后如果题目预算已耗尽，
    就不再生成追问，而是直接把面试推进到评价并结束
    （见 interview_termination 与 §9.3）。
    """

    def __init__(self, db: AsyncSession):
        self.db = db
        self.answer_service = InterviewAnswerService(db)
        self.question_service = InterviewQuestionService(db)
        self.session_repository = InterviewSessionRepository(db)
        self.session_service = InterviewSessionService(db)
        self.answer_analyzer = InterviewAnswerAnalyzer()
        self.follow_up_generator = InterviewFollowUpGenerator()
        self.usage_service = UsageService(db)

    async def process_answer(
        self,
        question_id: int,
        answer: str,
    ) -> dict:

        # 1. 获取当前问题
        question = await self.question_service.get_question(
            question_id=question_id
        )

        if question is None:
            raise ValueError(
                f"面试问题不存在: {question_id}"
            )

        # 2. 取会话并**先校验状态**。
        #
        # 先校验再干活：如果会话状态根本不允许答题，
        # 应该在写任何数据之前就失败，而不是写完回答才发现。
        interview_session = (
            await self.session_repository.get_by_id(
                question.session_id
            )
        )

        if interview_session is None:
            raise ValueError(
                f"面试会话不存在: {question.session_id}"
            )

        # 答题要走两步：asking → waiting_for_answer → evaluating。
        # 这里先确认这条路径可达，且失败时不会写任何数据。
        # 实际的状态推进在第 7 步（全部成功之后）。
        assert_transition(
            interview_session.status,
            SessionStatus.WAITING_FOR_ANSWER,
        )
        assert_transition(
            SessionStatus.WAITING_FOR_ANSWER,
            SessionStatus.EVALUATING,
        )

        # 3. 保存候选人回答
        saved_answer = await self.answer_service.create_answer(
            question_id=question_id,
            answer=answer,
        )

        # 4. 分析候选人回答
        #
        # 这是**线上答题路径**，显式使用线上采样数（默认 1 次）。
        # 每多采一次，用户等待时间就成倍增加；
        # 可复现性由离线评估（app/evaluation/interview/）保证。
        # 因此线上分数比评估报告里的分数波动更大，两者不可直接比较
        # （见 MIND_FLOW_PLAN.md §8A.8）。
        analysis = await self.answer_analyzer.analyze(
            question=question.question,
            answer=answer,
            repeats=online_analysis_samples(),
        )

        # 5. 判断是否还能继续出题。
        #
        # **必须先判断再转移**：这次 `→ evaluating` 的触发原因
        # 取决于是被预算停下的，还是正常完成分析后继续。
        # 若先无条件用 ANALYSIS_COMPLETED 记轨迹，
        # 预算结束时轨迹里就没有 budget_exhausted ——
        # 复盘时看不出是系统停的（曾因此漏记）。
        #
        # 顺序还有第二个理由：先判断再生成，避免白白多调一次 LLM。
        decision = decide_continue(
            questions_asked=interview_session.questions_asked,
            max_questions=interview_session.max_questions,
        )

        # 6. 走完 asking → waiting_for_answer → evaluating。
        #
        # **两步都要执行**，不能只 assert：
        # `assert_transition` 只校验、不改状态，
        # 因此"先 assert 两次再直接申请 evaluating"会失败 ——
        # 状态机看到的当前状态仍是 asking，而 asking 只能到
        # waiting_for_answer。这正是曾踩过的坑。
        #
        # 用 flush 而不是 commit：状态与之后的追问（或结束）
        # 要在同一次事务里落库，避免"状态已改但数据没跟"的中间态
        # （ADR-025）。
        await self.session_service.apply_transition(
            interview_session=interview_session,
            target_status=SessionStatus.WAITING_FOR_ANSWER,
            commit=False,
            trigger=TransitionTrigger.ANSWER_SUBMITTED,
        )

        await self.session_service.apply_transition(
            interview_session=interview_session,
            target_status=SessionStatus.EVALUATING,
            commit=False,
            trigger=(
                TransitionTrigger.ANALYSIS_COMPLETED
                if decision.should_continue
                else TransitionTrigger.BUDGET_EXHAUSTED
            ),
        )

        if not decision.should_continue:
            # 交给流程服务接管：它要求会话处于 evaluating，
            # 这里的状态已 flush 到事务里，因此它能读到。
            await self.db.flush()

            return await self._finish_by_budget(
                interview_session=interview_session,
                question=question,
                saved_answer=saved_answer,
                analysis=analysis,
                reason=decision.reason,
                detail=decision.detail,
            )

        # 7. 生成并保存追问
        #
        # 追问前先查**月度题目额度**（只读）。与常规题目同理：
        # 追问同样是一次 LLM 调用，没额度就不该白花。
        await self.usage_service.ensure_available(
            user_id=interview_session.user_id,
            metric=UsageMetric.QUESTION_GENERATED,
            quota=interview_session.user.monthly_question_quota,
        )

        follow_up_question = (
            await self.follow_up_generator.generate_follow_up(
                question=question.question,
                answer=answer,
                analysis=analysis,
            )
        )

        # LLM 已调用完，落库之前再判一次额度并扣减。
        #
        # 为什么在这里而不是更早：`ensure_available`（第 7 步开头）
        # 只读、不原子；若其他请求在两步之间把额度用完，
        # 必须在这里发现。`consume` 是"先自增再判定"，
        # 是本项目唯一的额度事实来源。
        #
        # 为什么在落库之前：此刻**分析结果与回答已提交**，
        # 但追问尚未落库。额度不足时抛出的 QuotaExceeded
        # 会让路由回滚这次自增 —— 用户已经产生的数据不受影响，
        # 也不会出现"追问已存但额度没扣"。
        #
        # 追问**必须**计入：它同样消耗一次分析 + 一次生成。
        # 只统计常规题目会让追问链完全不受额度约束 ——
        # 单场预算（questions_asked）曾因此少算，这里不再重犯。
        await self.usage_service.consume(
            user_id=interview_session.user_id,
            metric=UsageMetric.QUESTION_GENERATED,
            quota=interview_session.user.monthly_question_quota,
        )

        saved_follow_up = (
            await self.question_service.create_question(
                session_id=question.session_id,
                question=follow_up_question,
                question_type="follow_up",
                question_index=question.question_index + 1,
                context=question.question,
                # 追问没有独立的资料依据：它依据的是"上一个问题 + 回答"，
                # 而不是重新做检索。继承原问题的依据可以保留追溯链，
                # 也避免把"没有依据"误标成通用题。
                evidence_chunk_ids=list(
                    question.evidence_chunk_ids or []
                ),
                is_general=question.is_general,
            )
        )

        # 8. 追问已可发问，把状态交回 asking（准备问下一题）。
        #
        # 追问也要计入题目预算（`questions_asked`）：
        # 预算的语义是"问过的题目总数，含追问"，因为它同样消耗
        # 一次 LLM 分析 + 一次生成。只统计常规题目会让追问链
        # 完全不受预算约束 —— 曾因此导致预算判断始终少算。
        #
        # 状态、计数与追问在同一次提交里落库。
        interview_session.questions_asked = (
            interview_session.questions_asked + 1
        )
        updated_session = await self.session_service.apply_transition(
            interview_session=interview_session,
            target_status=SessionStatus.ASKING,
            commit=True,
            trigger=TransitionTrigger.FOLLOW_UP_GENERATED,
        )

        return {
            "answer": saved_answer,
            "analysis": analysis,
            "follow_up_question": saved_follow_up,
            "session_status": updated_session.status,
            "finished": False,
            "termination_reason": None,
            "evaluation": None,
        }

    async def _finish_by_budget(
        self,
        interview_session,
        question,
        saved_answer,
        analysis,
        reason: TerminationReason | None,
        detail: str,
    ) -> dict:
        """预算耗尽：不生成追问，直接评价并结束。

        为什么在答题流程里内联"结束"而不是让前端再调一次 `finish`：
        题量到顶是服务端的判断，用户并不知道"这题是最后一题"。
        若返回"没有下一题"再要求前端调 finish，
        中间任何一个环节失败都会让会话停在 evaluating。
        一次请求内走完，语义最清楚，也不留半成品状态。
        """

        from app.services.interview.interview_flow_service import (
            InterviewFlowService,
        )

        interview_session.termination_reason = (
            reason.value
            if reason
            else TerminationReason.BUDGET_EXHAUSTED.value
        )
        await self.session_repository.save(interview_session)

        # 用 BUDGET_EXHAUSTED 作为轨迹的触发原因，而不是
        # 默认的 USER_FINISHED —— 结束是系统因预算做出的决定，
        # 不是用户点的。轨迹不能在这点上说谎。
        result = await InterviewFlowService(
            self.db
        ).finish_interview(
            session_id=interview_session.id,
            trigger=TransitionTrigger.BUDGET_EXHAUSTED,
        )
        refreshed = await self.session_repository.get_by_id(
            interview_session.id
        )

        return {
            "answer": saved_answer,
            "analysis": analysis,
            # 没有下一题：这是"面试已结束"的明确信号
            "follow_up_question": None,
            "session_status": refreshed.status,
            "finished": True,
            "termination_reason": refreshed.termination_reason,
            "termination_detail": detail,
            "evaluation": result["saved_evaluation"],
        }
