from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.interview.interview_question import InterviewQuestion


class InterviewQuestionRepository:
    """面试问题数据访问层"""

    def __init__(self, session: AsyncSession):
        self.session = session

    async def create(
        self,
        session_id: int,
        question: str,
        question_type: str,
        question_index: int,
        context: str | None = None,
        evidence_chunk_ids: list[int] | None = None,
        is_general: bool = False,
    ) -> InterviewQuestion:
        """创建一个面试问题。

        evidence_chunk_ids 记录该问题依据的 DocumentChunk。
        §9.3 要求资料型问题可追溯依据，因此这个字段
        必须在创建时写入，而不是事后补查。

        is_general 显式标记通用能力题，避免"没有依据"
        与"依据为空"两种含义被混淆。
        """

        interview_question = InterviewQuestion(
            session_id=session_id,
            question=question,
            question_type=question_type,
            question_index=question_index,
            context=context,
            # 显式给空列表，避免 NULL 与 [] 两种语义混用
            evidence_chunk_ids=list(evidence_chunk_ids or []),
            is_general=is_general,
        )

        self.session.add(interview_question)

        await self.session.commit()
        await self.session.refresh(interview_question)

        return interview_question

    async def get_by_id(
        self,
        question_id: int,
    ) -> InterviewQuestion | None:
        result = await self.session.execute(
            select(InterviewQuestion).where(
                InterviewQuestion.id == question_id
            )
        )

        return result.scalar_one_or_none()

    async def get_by_session_id(
        self,
        session_id: int,
    ) -> list[InterviewQuestion]:
        result = await self.session.execute(
            select(InterviewQuestion)
            .where(
                InterviewQuestion.session_id == session_id
            )
            .order_by(
                InterviewQuestion.question_index.asc()
            )
        )

        return list(result.scalars().all())