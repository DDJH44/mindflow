import asyncio

from app.database.session import AsyncSessionLocal
from app.services.interview_question_service import InterviewQuestionService


async def main():
    async with AsyncSessionLocal() as db:
        service = InterviewQuestionService(db)

        question = await service.create_question(
            session_id=1,
            question="请介绍一下你在 MindFlow AI 面试助手项目中负责的后端开发工作。",
            question_type="technical",
            question_index=0,
            context="候选人简历与项目经历",
        )

        print("========== 问题保存成功 ==========")
        print(f"question_id: {question.id}")
        print(f"session_id: {question.session_id}")
        print(f"question_type: {question.question_type}")
        print(f"question_index: {question.question_index}")
        print(f"question: {question.question}")
        print("==================================")


if __name__ == "__main__":
    asyncio.run(main())