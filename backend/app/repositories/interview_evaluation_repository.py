from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.interview.interview_evaluation import InterviewEvaluation


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
