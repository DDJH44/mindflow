from fastapi import HTTPException, status

from app.repositories.interview_evaluation_repository import (
    InterviewEvaluationRepository,
)
from app.repositories.document_repository import DocumentRepository
from app.repositories.interview_session_repository import (
    InterviewSessionRepository,
)
from app.services.interview import interview_plan
from app.services.interview.context_builder import (
    InterviewContextBuilder,
)
from app.services.interview.evaluation_service import (
    InterviewEvaluationService,
)
from app.services.interview.interview_engine_service import (
    InterviewEngineService,
)
from app.services.interview.interview_state_machine import (
    TERMINAL_STATUSES,
    SessionStatus,
    TransitionTrigger,
    assert_transition,
    normalize_status,
)
from app.services.interview_session_service import (
    InterviewSessionService,
)


class InterviewFlowService:
    """面试流程的读写编排。

    职责边界：
    - 本服务负责"把业务链路的调用串起来 + 组装可读结果"。
    - 状态是否合法由状态机判定；数据库读写由仓储负责；
      单步业务（生成问题、处理作答、整场评价）由各自的 service 负责。

    存在的理由：结束面试需要把会话从当前状态推进到 evaluating
    再触发评价，这属于**流程编排**，不属于会话管理或评价本身。
    把它塞进任何一侧都会让那一侧承担额外职责（§4.1 分层原则）。
    """

    def __init__(self, db):
        self.db = db
        self.session_repository = InterviewSessionRepository(db)
        self.evaluation_repository = InterviewEvaluationRepository(db)
        self.session_service = InterviewSessionService(db)
        self.document_repository = DocumentRepository(db)

    async def _build_interview_plan(
        self,
        project_id: int,
    ) -> interview_plan.InterviewPlan:
        """按**该项目下真的有索引资料**的类型建立计划。

        ⚠️ 为什么只算已索引的类型：若把一个"上传了但索引失败"的类型
        排进计划，那一轮出题会检索不到任何东西，题目退化为通用题 ——
        而用户看起来像"检索坏了"。这里的取数口径与"题目必须有依据"
        是同一条底线。

        顺序沿用 `InterviewContextBuilder.DOCUMENT_TYPES` 的固定次序，
        而不是数据库返回顺序：计划要可复现，否则同样的资料在不同
        机器上会排出不同的面试顺序。
        """

        available = set(
            await self.document_repository.get_indexed_document_types(
                project_id=project_id
            )
        )

        ordered = [
            document_type
            for document_type in InterviewContextBuilder.DOCUMENT_TYPES
            if document_type in available
        ]

        return interview_plan.build_plan(ordered)

    async def start_interview(
        self,
        session_id: int,
        query: str | None = None,
        question_type: str = "technical",
    ) -> dict | None:
        """开始面试：准备上下文 → 生成首题 → 进入 asking。

        状态路径：`draft` → `preparing_context` → `planned`
        →（生成问题成功）→ `asking`。

        为什么把这三步合成一个端点：
        对调用方（前端）来说"开始面试"是一个动作，而不是三个状态转移。
        暴露三个转移会让前端必须自己实现状态机规则，
        并且在中途失败时留下半开状态（例如停在 planned 但没题目）。

        已暂停的会话会先恢复再开始：
        暂停在 `planned` 的会话，用户点"开始"的语义就是继续，
        要求他先调 resume 再调 start 没有意义。

        返回 None 表示会话不存在。
        """

        interview_session = (
            await self.session_repository.get_by_id(session_id)
        )

        if interview_session is None:
            return None

        current = normalize_status(interview_session.status)

        # 已暂停：先恢复到暂停前的状态。
        if current is SessionStatus.PAUSED:
            interview_session = (
                await self.session_service.resume_session(session_id)
            )
            current = normalize_status(interview_session.status)

        # 只允许从"还没开始"的状态启动。
        #
        # 不把 asking 也放进来：那属于"生成下一题"，
        # 与"开始面试"是两件事，混在一起会让重复点击
        # 悄悄多生成一道题。
        if current not in {
            SessionStatus.DRAFT,
            SessionStatus.PREPARING_CONTEXT,
            SessionStatus.PLANNED,
        }:
            raise ValueError(
                f"当前状态 {current.value} 无法开始面试；"
                "只有 draft / preparing_context / planned "
                "（以及暂停在这些状态的会话）可以开始"
            )

        # 逐步推进到 planned。每一步都经校验，不跳过状态机。
        #
        # 必须按当前状态**决定从哪一步开始**：
        # 状态机是单向的，对已经在 planned 的会话再执行
        # preparing_context 是**回退**，会被拒绝。
        if current is SessionStatus.DRAFT:
            pending_steps = [
                SessionStatus.PREPARING_CONTEXT,
                SessionStatus.PLANNED,
            ]
        elif current is SessionStatus.PREPARING_CONTEXT:
            pending_steps = [SessionStatus.PLANNED]
        else:
            # 已经是 planned（暂停在 planned 的会话恢复后也走这里）
            pending_steps = []

        for intermediate in pending_steps:
            interview_session = (
                await self.session_service.apply_transition(
                    interview_session=interview_session,
                    target_status=intermediate,
                    commit=True,
                    trigger=TransitionTrigger.INTERVIEW_STARTED,
                )
            )

        # 建立面试计划（按**有资料的材料类型**轮转）。
        #
        # 为什么要做：此前**从第二题起全是追问第一题的回答**，
        # 于是整场面试都在第一次检索命中的那块材料上打转，
        # 其它已上传的材料从未被问到（见 interview_plan 的模块说明）。
        #
        # 为什么计划在这里建：计划描述的是"这场面试打算覆盖什么"，
        # 它必须在第一题之前确定 —— 否则第一题无法落在计划的第一项上。
        plan = await self._build_interview_plan(
            project_id=interview_session.project_id,
        )

        # 把第一题落到计划的第一项上（此时还没有任何主题被覆盖，
        # `start_topic` 会取第一个未覆盖主题）。
        first_topic = interview_plan.start_topic(plan)

        interview_session.plan_snapshot = plan.to_dict()

        # 生成首题。索引与状态由 engine service 负责。
        question = await InterviewEngineService(
            self.db
        ).generate_and_save_question(
            session_id=session_id,
            project_id=interview_session.project_id,
            # 没给方向时用目标岗位，再退化为面试类型 ——
            # 让检索 query 至少有语义，而不是空字符串。
            query=(
                query
                or interview_session.target_role
                or f"{interview_session.interview_type} 面试"
            ),
            question_type=question_type,
        )

        # 首题已发出 → 它的主题算"覆盖"。
        #
        # 覆盖的语义是"**我们问过**"，不是"候选人答得好" ——
        # 若按回答质量决定是否覆盖，答得差的人会被反复问同一主题，
        # 面试就变成针对弱点的拷问，而不是评估。
        interview_plan.mark_covered(plan, first_topic.id)

        interview_session.plan_snapshot = plan.to_dict()

        await self.session_repository.save(interview_session)

        refreshed = await self.session_repository.get_by_id(
            session_id
        )

        return {
            "question": question,
            "session": refreshed,
        }

    async def get_status_history(
        self,
        session_id: int,
    ) -> list[dict]:
        """返回会话的状态转移轨迹，按发生顺序正序。

        轨迹是审计记录，因此原样返回 `from_status` / `to_status`
        （含历史上的 "created"），不做规范化 ——
        规范化会让"当时库里是什么"变得不可考。
        """

        rows = await self.session_repository.get_status_history(
            session_id=session_id
        )

        return [
            {
                "id": row.id,
                "from_status": row.from_status,
                "to_status": row.to_status,
                "trigger": row.trigger,
                "created_at": row.created_at,
            }
            for row in rows
        ]

    async def get_questions_with_answers(
        self,
        session_id: int,
    ) -> list[dict]:
        """返回会话的全部题目及各自的回答，按题目顺序排列。"""

        rows = (
            await self.session_repository
            .get_session_questions_with_answers(
                session_id=session_id
            )
        )

        items = []

        for question, answer in rows:
            items.append(
                {
                    "id": question.id,
                    "session_id": question.session_id,
                    "question": question.question,
                    "question_type": question.question_type,
                    "question_index": question.question_index,
                    "context": question.context,
                    "evidence_chunk_ids": list(
                        question.evidence_chunk_ids or []
                    ),
                    "is_general": question.is_general,
                    "created_at": question.created_at,
                    "answer": answer.answer if answer else None,
                    "answered_at": (
                        answer.created_at if answer else None
                    ),
                    # 逐轮分析的留档（复盘用）。
                    #
                    # 只在**回答存在**时才有值；历史记录（本功能之前）
                    # 为 None —— 那是事实，前端应据此隐藏该区块，
                    # 而不是显示"无数据"让人以为坏了。
                    "analysis": (
                        getattr(answer, "analysis_snapshot", None)
                        if answer
                        else None
                    ),
                }
            )

        return items

    async def get_latest_evaluation(self, session_id: int) -> dict | None:
        """返回最近一次整场评价（未评价时为 None）。

        取最新而不是全部：ADR-026 规定 completed 是终态、
        不支持重评，因此正常情况只有一条。若历史上存在多条，
        取最新符合 `InterviewEvaluationRepository` 的既有约定。
        """

        evaluation = (
            await self.evaluation_repository.get_by_session_id(
                session_id
            )
        )

        if evaluation is None:
            return None

        return {
            "id": evaluation.id,
            "overall_score": evaluation.overall_score,
            "technical_score": evaluation.technical_score,
            "project_score": evaluation.project_score,
            "communication_score": evaluation.communication_score,
            "strengths": list(evaluation.strengths or []),
            "weaknesses": list(evaluation.weaknesses or []),
            "suggestions": list(evaluation.suggestions or []),
            "feedback": evaluation.feedback,
            "scoring_details": evaluation.scoring_details or {},
            # 可追溯结论。**None 与 [] 含义不同**：
            # None = 记录早于该功能（UI 整块隐藏）；
            # []   = 有该功能，但这次没有带出处的结论。
            "findings": evaluation.findings,
            "created_at": evaluation.created_at,
        }

    async def finish_interview(
        self,
        session_id: int,
        trigger: TransitionTrigger | str = (
            TransitionTrigger.USER_FINISHED
        ),
    ):
        """结束面试并生成整场评价。

        状态路径：当前状态 → evaluating →（评价成功后）
        summarizing → completed。

        `trigger` 由调用方指定，因为"为什么结束"会写进轨迹：
        - 用户点结束 → USER_FINISHED
        - 题量到预算自动结束 → BUDGET_EXHAUSTED

        不能一律写 USER_FINISHED：那会让审计记录**说谎** ——
        复盘时看到"用户主动结束"，而实际是系统因预算停止的。

        为什么要先推进到 evaluating：
        `InterviewEvaluationService.evaluate_session` 要求会话
        处于 evaluating（它只负责评价，不负责把会话搬到可评价状态）。
        因此"搬运"这一步属于流程编排，放在本服务。

        失败语义（ADR-025）：推进到 evaluating 之后若评价失败，
        会话停在 evaluating —— 该状态**可以直接重试**，
        不需要额外规则。
        """

        interview_session = (
            await self.session_repository.get_by_id(session_id)
        )

        if interview_session is None:
            return None

        # 把会话搬到可评价状态。
        #
        # 分情况处理：
        #
        # 1. 已在 `evaluating` —— 答题流程内触发预算结束时就是这种：
        #    那边已经把状态 flush 成 evaluating 才交到这里。
        #    此时**不能再走** asking → waiting_for_answer：
        #    那是回退，会被状态机拒绝。直接进入评价。
        #
        # 2. `paused` —— **先恢复，再按恢复后的状态处理**。
        #    这一步很容易漏：用户的意图是"结束面试"，
        #    而 paused 不是 paused 会话的唯一合法后继只有 cancelled，
        #    因此不先恢复就会得到
        #    `非法状态转移：paused → waiting_for_answer`（D56）。
        #    实测用户回答一题后暂停、再点"结束并生成报告"就是这个报错。
        #
        # 3. `asking` —— 正常路径：用户主动结束。
        #    必须显式走完整链条，因为 asking → evaluating 不是一步
        #    （状态表里 asking 的后继是 waiting_for_answer）。
        #
        # 4. `waiting_for_answer` —— 作答已提交、还没走完分析。
        #    可以直接进入 evaluating（状态表允许），
        #    这也是 paused 会话恢复后可能落到的状态。
        #
        # 其他状态一律拒绝，且**按状态给出对的理由**。
        #
        # 分两类，理由完全不同 —— 用同一句话会误导人：
        # - 终态（completed / cancelled / failed）：面试已经结束过。
        #   重复结束是状态冲突，调用方应得到 409。
        # - 未开始（draft / preparing_context / planned）：
        #   一题都还没问，结束它没有意义，应先去开始面试。
        current = normalize_status(interview_session.status)

        if current is SessionStatus.PAUSED:
            resumed = await self.session_service.resume_session(
                interview_session.id
            )

            if resumed is None:
                return None

            interview_session = resumed
            current = normalize_status(interview_session.status)

        if current is SessionStatus.EVALUATING:
            pass

        elif current in (
            SessionStatus.ASKING,
            SessionStatus.WAITING_FOR_ANSWER,
        ):
            # 从这两个状态都要经过 waiting_for_answer 再进 evaluating。
            # 已在 waiting_for_answer 时跳过第一步（自己到自己不是转移）。
            steps = (
                [SessionStatus.EVALUATING]
                if current is SessionStatus.WAITING_FOR_ANSWER
                else [
                    SessionStatus.WAITING_FOR_ANSWER,
                    SessionStatus.EVALUATING,
                ]
            )

            assert_transition(
                interview_session.status,
                steps[0],
                resume_status=None,
            )

            for intermediate in steps:
                await self.session_service.apply_transition(
                    interview_session=interview_session,
                    target_status=intermediate,
                    commit=True,
                    trigger=trigger,
                )

        elif current in TERMINAL_STATUSES:
            # 已经结束过 —— 这是**状态冲突（409）**，不是参数错误（400）。
            #
            # 为什么直接抛 HTTPException：本服务的其它方法
            # （如 `resume_session`）已按这个方式报告不可执行的操作，
            # 跟随既有惯例。若走 InvalidTransitionError，构造函数会拼出
            # "非法状态转移：completed → completed；允许的目标状态为 []" ——
            # 用户看不出"这场面试已经结束过了"。
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail=(
                    f"这场面试已经结束过了（当前状态 {current.value}），"
                    "不能重复结束。若要重看结果，请打开报告页。"
                ),
            )

        else:
            raise ValueError(
                f"当前状态 {current.value} 还不能结束面试 —— "
                "还没有生成任何题目。请先开始面试。"
            )

        return await InterviewEvaluationService(
            self.db
        ).evaluate_session(session_id=session_id)
