import asyncio

from app.database.session import AsyncSessionLocal
from app.services.interview.question_generator import InterviewQuestionGenerator


async def main():
    async with AsyncSessionLocal() as db:
        generator = InterviewQuestionGenerator(db)

        question = await generator.generate_question(
            project_id=3,
            query="考察候选人的后端开发能力和项目实践经验",
        )

        print("========== 面试问题 ==========")
        print(question)
        print("===============================")


if __name__ == "__main__":
    asyncio.run(main())