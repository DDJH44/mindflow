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
from app.services.interview import interview_plan
from app.services.interview.question_generator import (
    InterviewQuestionGenerator,
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
        self.question_generator = InterviewQuestionGenerator(db)
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

        # 4.1 把分析留档到回答记录（复盘用）。
        #
        # 为什么在这里而不是等流程结束：
        # 这份分析**只在这一刻存在于内存里** —— 后面的追问生成、
        # 状态转移一旦抛异常，它就永远丢了。先落盘再往下走。
        #
        # 为什么失败不中断：见 `save_analysis` 的说明 ——
        # 它只影响"复盘数据"，而用户的回答已经落库。
        await self.answer_service.save_analysis(
            answer_id=saved_answer.id,
            analysis=analysis,
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

        # 7. 由**计划**决定下一步：继续追问 / 换主题 / 开新一轮。
        #
        # 为什么需要这一步：在计划之前，**从第二题起全是追问第一题的
        # 回答**，整场面试都围绕第一次检索命中的那块材料打转，
        # 其它已上传的材料从未被问到（见 interview_plan 的说明）。
        #
        # `plan` 为 None 表示这是**本功能之前创建的会话**（或快照被
        # 改坏）。此时退化为原来的"只追问"行为 —— 旧会话照常能答完，
        # 不该因为新增了计划而失效。
        plan = interview_plan.InterviewPlan.from_dict(
            interview_session.plan_snapshot
        )

        action = (
            interview_plan.decide_next_action(plan)
            if plan is not None
            else interview_plan.ACTION_FOLLOW_UP
        )

        # 主题都问过一遍 → 开新一轮，而不是结束面试。
        #
        # 为什么不结束：`max_questions` 是用户设定的题量，
        # 而主题数取决于"上传了几类材料"—— 那不该决定面试多长
        # （见 `decide_next_action` 里被否掉的软性终止方案）。
        if action == interview_plan.ACTION_NEW_PASS:
            interview_plan.start_new_pass(plan)
            action = interview_plan.ACTION_NEW_TOPIC

        # 换主题：为下一个未覆盖主题生成一道**主题题**（不是追问）。
        if action == interview_plan.ACTION_NEW_TOPIC:
            return await self._ask_next_topic(
                interview_session=interview_session,
                plan=plan,
                question=question,
                saved_answer=saved_answer,
                analysis=analysis,
            )

        # 7.1 继续追问（原有路径）。
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

        # 记一次追问到计划里：下次决策据此判断"这个主题问够了没有"。
        # 不记的话 `follow_ups_on_current` 永远是 0，
        # 于是会**无限追问同一个主题**，永远不换题。
        if plan is not None:
            interview_plan.record_follow_up(plan)
            interview_session.plan_snapshot = plan.to_dict()

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

    async def _ask_next_topic(
        self,
        *,
        interview_session,
        plan: interview_plan.InterviewPlan,
        question,
        saved_answer,
        analysis,
    ) -> dict:
        """为下一个未覆盖主题生成一道**主题题**（不是追问）。

        ## 为什么需要它

        在此之前，回答完一题之后**只会生成追问**。于是整场面试
        都在第一次检索命中的那块材料上打转，其它已上传的材料
        （岗位要求、项目细节、代码）从未被问到 ——
        而那正是"上传四类材料"本该带来的价值。

        ## 顺序为什么是"先查额度 → 生成 → 再扣额度 → 落库"

        与 `InterviewEngineService.generate_and_save_question` 保持
        一致：第一次检查是**只读**的，目的是不让没额度的人白白
        触发一次 LLM 调用；第二次 `consume` 是"先自增再判定"，
        是本项目唯一的额度事实来源。

        此时尚未提交任何业务数据，因此额度不足抛出的
        `QuotaExceeded` 可以安全回滚 —— 不会留下"题目已存但计数被吞"。
        """

        topic = interview_plan.start_topic(plan)

        direction = (
            interview_session.target_role
            or f"{interview_session.interview_type} 面试"
        )

        query = interview_plan.topic_query(plan, direction=direction)

        await self.usage_service.ensure_available(
            user_id=interview_session.user_id,
            metric=UsageMetric.QUESTION_GENERATED,
            quota=interview_session.user.monthly_question_quota,
        )

        generated = (
            await self.question_generator.generate_question_with_evidence(
                project_id=interview_session.project_id,
                query=query,
            )
        )

        await self.usage_service.consume(
            user_id=interview_session.user_id,
            metric=UsageMetric.QUESTION_GENERATED,
            quota=interview_session.user.monthly_question_quota,
        )

        saved_question = await self.question_service.create_question(
            session_id=interview_session.id,
            question=generated["question"],
            # `topic` 与 `follow_up` 分开：复盘时要能看出
            # "这道题是在换新材料"还是"在深挖刚才的回答"。
            question_type="topic",
            question_index=question.question_index + 1,
            context=query,
            evidence_chunk_ids=generated["evidence_chunk_ids"],
            is_general=generated["is_general"],
        )

        # 主题已问过 → 标记覆盖（覆盖 = 我们问过，不是"答得好"）。
        interview_plan.mark_covered(plan, topic.id)

        interview_session.plan_snapshot = plan.to_dict()
        interview_session.questions_asked = (
            interview_session.questions_asked + 1
        )

        updated_session = await self.session_service.apply_transition(
            interview_session=interview_session,
            target_status=SessionStatus.ASKING,
            commit=True,
            trigger=TransitionTrigger.QUESTION_GENERATED,
        )

        return {
            "answer": saved_answer,
            "analysis": analysis,
            # 复用 `follow_up_question` 这个字段名：对前端来说
            # "下一道要回答的题"是同一种东西，多一个字段会让
            # 前端要判断两个来源。
            "follow_up_question": saved_question,
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
