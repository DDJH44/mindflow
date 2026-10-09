from sqlalchemy.ext.asyncio import AsyncSession

from app.repositories.interview_question_repository import (
    InterviewQuestionRepository,
)


class InterviewQuestionService:
    """面试问题业务服务"""

    def __init__(self, db: AsyncSession):
        self.repository = InterviewQuestionRepository(db)

    async def create_question(
        self,
        session_id: int,
        question: str,
        question_type: str,
        question_index: int,
        context: str | None = None,
        evidence_chunk_ids: list[int] | None = None,
        is_general: bool = False,
    ):
        return await self.repository.create(
            session_id=session_id,
            question=question,
            question_type=question_type,
            question_index=question_index,
            context=context,
            evidence_chunk_ids=evidence_chunk_ids,
            is_general=is_general,
        )

    async def get_question(
        self,
        question_id: int,
    ):
        return await self.repository.get_by_id(
            question_id=question_id
        )

    async def get_session_questions(
        self,
        session_id: int,
    ):
        return await self.repository.get_by_session_id(
            session_id=session_id
        )