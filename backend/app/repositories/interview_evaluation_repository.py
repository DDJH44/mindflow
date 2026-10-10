from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.interview.interview_evaluation import InterviewEvaluation
from app.models.interview.interview_session import InterviewSession


class InterviewEvaluationRepository:
    def __init__(self, session: AsyncSession):
        self.session = session

    async def create(
        self,
        session_id: int,
        overall_score: int,
        technical_score: int,
        project_score: int,
        communication_score: int,
        feedback: str,
        strengths: list[str] | None = None,
        weaknesses: list[str] | None = None,
        suggestions: list[str] | None = None,
        scoring_details: dict | None = None,
    ) -> InterviewEvaluation:
        """写入一条整场评价。

        strengths / weaknesses / suggestions 是 Phase 6
        「能力画像与训练建议」的前置数据，必须落库，
        不能只留在 LLM 返回值里。

        scoring_details 记录锚点与采样明细，用于事后解释分数
        （线上与离线采样数不同，见 ADR-021）。
        """

        evaluation = InterviewEvaluation(
            session_id=session_id,
            overall_score=overall_score,
            technical_score=technical_score,
            project_score=project_score,
            communication_score=communication_score,
            feedback=feedback,
            # 显式给空列表/空字典，避免 NULL 与 [] 两种语义混用
            strengths=list(strengths or []),
            weaknesses=list(weaknesses or []),
            suggestions=list(suggestions or []),
            scoring_details=dict(scoring_details or {}),
        )

        self.session.add(evaluation)
        await self.session.commit()
        await self.session.refresh(evaluation)

        return evaluation

    async def get_by_session_id(
        self,
        session_id: int,
    ) -> InterviewEvaluation | None:
        result = await self.session.execute(
            select(InterviewEvaluation)
            .where(
                InterviewEvaluation.session_id == session_id
            )
            .order_by(
                InterviewEvaluation.created_at.desc()
            )
        )

        return result.scalars().first()

    async def get_by_user(
        self,
        user_id: int,
        project_id: int | None = None,
        limit: int = 50,
        structured_only: bool = True,
    ) -> list[tuple[InterviewEvaluation, InterviewSession]]:
        """按用户取整场评价，**按时间正序**（旧 → 新）。

        为什么返回元组而不是纯 evaluation：能力画像要展示
        "哪一场、什么岗位、哪个项目"，这些在会话上，
        而评价表只有 `session_id`。让调用方再查一次会话
        会产生 N+1。

        为什么用**正序**：画像要看趋势，趋势必须按时间从旧到新。
        仓储默认倒序（列表页要"最近的在前"）在这里是错的 ——
        调用方若忘了反转，趋势图就会画反，
        而那种错误看起来只是"分数在下降"。

        `structured_only` 默认开启，排除**三组结构化内容全空**的
        评价。那种记录来自 `strengths` / `weaknesses` / `suggestions`
        落库（D20）之前 —— 它们只有四项分数与 `feedback`，
        进入趋势统计会**用占位数据撑大区间**，而用户无从分辨。
        实测 `mindflow` 名下 3 条这样的记录把画像撑成
        "共 6 次测量、区间 30–90"。

        为什么不直接删掉它们：`feedback` 是**真实内容**，
        报告页还在用。清理数据是不可逆的，过滤视图是可逆的 ——
        因此这里选择过滤。
        """

        statement = (
            select(InterviewEvaluation, InterviewSession)
            .join(
                InterviewSession,
                InterviewSession.id
                == InterviewEvaluation.session_id,
            )
            .where(InterviewSession.user_id == user_id)
        )

        if structured_only:
            statement = statement.where(
                or_(
                    func.json_array_length(
                        InterviewEvaluation.strengths
                    )
                    > 0,
                    func.json_array_length(
                        InterviewEvaluation.weaknesses
                    )
                    > 0,
                    func.json_array_length(
                        InterviewEvaluation.suggestions
                    )
                    > 0,
                )
            )

        if project_id is not None:
            statement = statement.where(
                InterviewSession.project_id == project_id
            )

        statement = (
            statement.order_by(InterviewEvaluation.created_at.asc())
            .limit(limit)
        )

        result = await self.session.execute(statement)

        return list(result.all())

    async def count_by_user(
        self,
        user_id: int,
        project_id: int | None = None,
        structured_only: bool = True,
    ) -> int:
        """该用户可用于画像的评价数。"""

        statement = (
            select(func.count(InterviewEvaluation.id))
            .join(
                InterviewSession,
                InterviewSession.id
                == InterviewEvaluation.session_id,
            )
            .where(InterviewSession.user_id == user_id)
        )

        if structured_only:
            statement = statement.where(
                or_(
                    func.json_array_length(
                        InterviewEvaluation.strengths
                    )
                    > 0,
                    func.json_array_length(
                        InterviewEvaluation.weaknesses
                    )
                    > 0,
                    func.json_array_length(
                        InterviewEvaluation.suggestions
                    )
                    > 0,
                )
            )

        if project_id is not None:
            statement = statement.where(
                InterviewSession.project_id == project_id
            )

        result = await self.session.execute(statement)

        return result.scalar_one()
