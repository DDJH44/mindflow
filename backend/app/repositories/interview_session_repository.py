from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.interview.interview_session import InterviewSession
from app.models.interview.interview_question import InterviewQuestion
from app.models.interview.interview_answer import InterviewAnswer
from app.models.interview.interview_status_history import (
    InterviewStatusHistory,
)


class InterviewSessionRepository:
    def __init__(self, session: AsyncSession):
        self.session = session

    async def get_status_history(
        self,
        session_id: int,
    ) -> list[InterviewStatusHistory]:
        """按时间正序返回会话的状态转移轨迹。

        正序（而不是倒序）：轨迹是**故事**，要按发生顺序读。
        需要"最近一次"的调用方自己取 `[-1]`。
        """

        result = await self.session.execute(
            select(InterviewStatusHistory)
            .where(InterviewStatusHistory.session_id == session_id)
            .order_by(
                InterviewStatusHistory.created_at.asc(),
                InterviewStatusHistory.id.asc(),
            )
        )

        return list(result.scalars().all())

    async def create(
        self,
        project_id: int,
        user_id: int,
        interview_type: str = "technical",
        target_role: str | None = None,
        max_questions: int = 8,
    ) -> InterviewSession:
        """创建面试会话。

        初始状态是 draft（状态机起点，见 interview_state_machine）。
        历史数据里的 "created" 语义等同于 draft，
        由状态机的 LEGACY_STATUS_MAP 兼容，不需要数据回填。

        `user_id` 由 service 层核对过与项目所有者一致后才传进来 ——
        仓储不做业务校验，但要求它必填，避免"忘记写所有者"
        这种错误静默通过。
        """

        interview_session = InterviewSession(
            user_id=user_id,
            project_id=project_id,
            interview_type=interview_type,
            target_role=target_role,
            status="draft",
            current_question_index=0,
            # 预算与计数必须显式初始化：
            # 依赖模型 default 在"对象未 flush 就被读取"时拿不到值。
            max_questions=max_questions,
            questions_asked=0,
            plan_snapshot={},
        )

        self.session.add(interview_session)

        await self.session.commit()
        await self.session.refresh(interview_session)

        return interview_session

    async def update_status(
        self,
        interview_session: InterviewSession,
        status: str,
        commit: bool = True,
    ) -> InterviewSession:
        """更新会话状态。

        只负责写库；**合法转移的校验在 service 层**
        （见 InterviewSessionService.transition_status），
        仓储层不承担业务规则。

        commit=False 时只 flush 不提交，把提交时机交给调用方。
        这使"业务数据 + 状态"能在**同一次提交**里落库：
        否则状态会被提前提交，一旦后续的 LLM 调用失败，
        库里就留下一个与业务数据不匹配的状态（见 ADR-025）。

        暂停相关的字段（resume_status / pause_reason / paused_at）
        由 service 层直接设在 ORM 对象上，本方法一并提交 ——
        仓储保持"哑"的职责，不承担"该不该清空暂停信息"这类判断。
        """

        interview_session.status = status

        self.session.add(interview_session)

        if commit:
            await self.session.commit()
            await self.session.refresh(interview_session)
        else:
            await self.session.flush()

        return interview_session

    async def save(
        self,
        interview_session: InterviewSession,
    ) -> InterviewSession:
        """提交对会话对象的任意字段改动。

        与 `update_status` 的区别：本方法不做任何字段级别的语义约定，
        只负责"把当前对象状态落库"。

        用途：需要**同时**改多个字段时（例如生成题目后
        索引与状态一起前进），逐个字段写一个专用方法没有意义 ——
        调用方直接改对象、调用本方法提交，语义更清楚。
        """

        self.session.add(interview_session)
        await self.session.commit()
        await self.session.refresh(interview_session)

        return interview_session

    async def get_by_id(
        self,
        interview_id: int,
    ) -> InterviewSession | None:
        result = await self.session.execute(
            select(InterviewSession).where(
                InterviewSession.id == interview_id
            )
        )

        return result.scalar_one_or_none()

    async def get_session_questions_with_answers(
        self,
        session_id: int,
    ):
        result = await self.session.execute(
            select(
                InterviewQuestion,
                InterviewAnswer,
            )
            .outerjoin(
                InterviewAnswer,
                InterviewAnswer.question_id == InterviewQuestion.id,
            )
            .where(
                InterviewQuestion.session_id == session_id
            )
            .order_by(
                InterviewQuestion.question_index.asc()
            )
        )

        return result.all()