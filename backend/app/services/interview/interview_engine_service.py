from sqlalchemy.ext.asyncio import AsyncSession

from app.repositories.interview_session_repository import (
    InterviewSessionRepository,
)
from app.services.interview.interview_state_machine import (
    SessionStatus,
    TransitionTrigger,
    normalize_status,
)
from app.services.interview.question_generator import (
    InterviewQuestionGenerator,
)
from app.services.interview_question_service import (
    InterviewQuestionService,
)
from app.services.interview_session_service import (
    InterviewSessionService,
)
from app.services.usage.usage_quota import UsageMetric
from app.services.usage.usage_service import UsageService


# 允许生成常规（非追问）题目的状态。
#
# planned：首题，这是面试正式开始的那一步。
# asking：已经有题在等回答，此时再生成属于"下一题"。
#
# 不包含 draft / preparing_context：那两步是 start_interview 的职责，
# 直接跳到生成题目会跳过上下文准备。
ALLOWED_SOURCE_STATUSES: frozenset[SessionStatus] = frozenset(
    {SessionStatus.PLANNED, SessionStatus.ASKING}
)


class InterviewEngineService:
    """面试流程协调服务。

    状态推进遵循 ADR-025：**只在外层调用成功后才推进状态**。
    生成问题时 LLM 可能失败，因此先校验状态，
    等问题真正保存成功后才提交 `→ asking`。

    为什么生成问题要推进到 asking：
    「问题已经就绪、等待候选人回答」正是 asking 的语义。
    若不推进，第一题的会话会一直停在 planned，
    而 `InterviewTurnService` 要求会话处于 asking 才允许答题 ——
    两者会直接矛盾。
    """

    def __init__(self, db: AsyncSession):
        self.session_repository = InterviewSessionRepository(db)
        self.session_service = InterviewSessionService(db)
        self.question_generator = InterviewQuestionGenerator(db)
        self.question_service = InterviewQuestionService(db)
        self.usage_service = UsageService(db)

    async def generate_and_save_question(
        self,
        session_id: int,
        project_id: int,
        query: str,
        question_type: str = "technical",
        question_index: int | None = None,
    ):
        """生成并保存一道常规题目（不是追问）。

        `question_index` 不传时按会话的 `current_question_index` 自动取值。
        让调用方自己传索引会产生不一致 —— 首题应传 0 还是 1？
        原先由脚本调用时靠人工约定，接口化之后必须由服务端统一决定。
        """

        interview_session = (
            await self.session_repository.get_by_id(session_id)
        )

        if interview_session is None:
            raise ValueError(f"面试会话不存在: {session_id}")

        # 校验当前状态是否允许生成常规题目。
        #
        # 注意这里**不能**用 assert_transition(status, ASKING)：
        # 它校验的是"能否到达 asking"，而 asking → asking 不在转移表里
        # （自己到自己不是转移），会把"已经处于 asking、要生成下一题"
        # 这种情况错误地拒绝。
        current_status = normalize_status(
            interview_session.status
        )

        if current_status not in ALLOWED_SOURCE_STATUSES:
            raise ValueError(
                f"当前状态 {current_status.value} 不允许生成常规题目；"
                f"允许的状态为 "
                f"{sorted(item.value for item in ALLOWED_SOURCE_STATUSES)}"
            )

        # 索引由服务端决定，避免调用方传错
        resolved_index = (
            interview_session.current_question_index
            if question_index is None
            else question_index
        )

        # 生成题目**之前**先查题目额度（只读，不消费）。
        #
        # 顺序理由：LLM 调用是真花钱的。若等到生成完再判额度，
        # 没额度的用户照样能触发一次调用。
        # 这里只读不写，所以**不能**作为最终的额度判定。
        await self.usage_service.ensure_available(
            user_id=interview_session.user_id,
            metric=UsageMetric.QUESTION_GENERATED,
            quota=interview_session.user.monthly_question_quota,
        )

        # 根据候选人资料生成问题，并同时取回其资料依据。
        #
        # 依据必须在这里捕获：检索就发生在这一步，
        # 事后重新检索会得到另一批 chunk（见 question_generator 的说明）。
        generated = (
            await self.question_generator.generate_question_with_evidence(
                project_id=project_id,
                query=query,
            )
        )

        # LLM 已调用完，落库之前**再判一次**额度。
        #
        # 为什么需要第二次：第一次检查只读、不原子。
        # 若其他请求在这两步之间把额度用完了，这里必须发现，
        # 否则会出现"题目已提交但额度没扣"。
        #
        # 为什么用 `consume` 而不是 `ensure_available`：
        # consume 是"先自增再判定"，是本项目唯一的额度事实来源。
        # 它同时完成"扣额度"与"验额度"两件事。
        #
        # ⚠️ 此时还**没有**提交任何业务数据，因此额度不足时
        # 抛出的 QuotaExceeded 可以安全回滚（见路由层），
        # 不会留下"题目已存但计数被吞"的不一致状态。
        await self.usage_service.consume(
            user_id=interview_session.user_id,
            metric=UsageMetric.QUESTION_GENERATED,
            quota=interview_session.user.monthly_question_quota,
        )

        # 额度已扣，保存问题，并记录资料依据与"是否通用题"。
        saved_question = await self.question_service.create_question(
            session_id=session_id,
            question=generated["question"],
            question_type=question_type,
            question_index=resolved_index,
            context=query,
            evidence_chunk_ids=generated["evidence_chunk_ids"],
            is_general=generated["is_general"],
        )

        # 问题已保存，此时才推进：索引前进 + 计数 +1 + 状态置 asking。
        #
        # 三者必须与问题在同一次提交里落库，
        # 避免"问题已存但计数没跟"的中间状态 ——
        # 那会让预算判断少算一道题，实际问的比预算多。
        #
        # 这里是**直接改写 status**（而不是 apply_transition）：
        # planned 的唯一常规后继就是 asking，不需要状态机再选一次。
        # 但直接写会让轨迹缺口，因此调 record_status_change 补记，
        # 且仍然只 add 不 commit，与状态一起提交。
        await self.session_service.record_status_change(
            interview_session=interview_session,
            to_status=SessionStatus.ASKING,
            trigger=TransitionTrigger.QUESTION_GENERATED,
        )

        interview_session.status = SessionStatus.ASKING.value
        interview_session.current_question_index = resolved_index + 1
        interview_session.questions_asked = (
            interview_session.questions_asked + 1
        )

        await self.session_repository.save(interview_session)

        return saved_question