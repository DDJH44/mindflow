from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.interview.interview_answer import InterviewAnswer


class InterviewAnswerRepository:
    """面试回答数据访问层"""

    def __init__(self, session: AsyncSession):
        self.session = session

    async def create(
        self,
        question_id: int,
        answer: str,
    ) -> InterviewAnswer:
        interview_answer = InterviewAnswer(
            question_id=question_id,
            answer=answer,
        )

        self.session.add(interview_answer)

        await self.session.commit()
        await self.session.refresh(interview_answer)

        return interview_answer

    async def get_by_id(
        self,
        answer_id: int,
    ) -> InterviewAnswer | None:

        result = await self.session.execute(
            select(InterviewAnswer).where(
                InterviewAnswer.id == answer_id
            )
        )

        return result.scalar_one_or_none()

    async def get_by_question_id(
        self,
        question_id: int,
    ) -> InterviewAnswer | None:

        result = await self.session.execute(
            select(InterviewAnswer)
            .where(
                InterviewAnswer.question_id == question_id
            )
            .order_by(
                InterviewAnswer.created_at.desc()
            )
        )

        return result.scalars().first()