from sqlalchemy import select, update
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

    async def save_analysis(
        self,
        answer_id: int,
        snapshot: dict,
    ) -> None:
        """把逐轮分析写进回答记录。

        **单独一步、在分析完成之后调用**，因为分析要调模型
        （数秒到数十秒），而回答本身必须**先落库** ——
        否则模型一失败，用户刚打的字就丢了。

        这个顺序是刻意的：
            ① 先存回答（用户数据优先）
            ② 再调模型分析
            ③ 分析成功后补写快照

        因此第 ③ 步失败只会丢掉"复盘数据"，不会丢用户的回答。
        """

        await self.session.execute(
            update(InterviewAnswer)
            .where(InterviewAnswer.id == answer_id)
            .values(analysis_snapshot=snapshot)
        )

        await self.session.commit()

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