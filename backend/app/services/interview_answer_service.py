from sqlalchemy.ext.asyncio import AsyncSession

from app.repositories.interview_answer_repository import (
    InterviewAnswerRepository,
)


class InterviewAnswerService:
    """面试回答业务服务"""

    def __init__(self, db: AsyncSession):
        self.repository = InterviewAnswerRepository(db)

    async def create_answer(
        self,
        question_id: int,
        answer: str,
    ):
        return await self.repository.create(
            question_id=question_id,
            answer=answer,
        )

    async def get_answer(
        self,
        answer_id: int,
    ):
        return await self.repository.get_by_id(
            answer_id=answer_id
        )

    async def get_question_answer(
        self,
        question_id: int,
    ):
        return await self.repository.get_by_question_id(
            question_id=question_id
        )