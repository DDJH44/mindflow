import asyncio

from app.database.session import AsyncSessionLocal
from app.repositories.interview_session_repository import InterviewSessionRepository


async def main():
    async with AsyncSessionLocal() as db:
        repository = InterviewSessionRepository(db)

        rows = await repository.get_session_questions_with_answers(
            session_id=1
        )

        print(f"共查询到 {len(rows)} 条记录")

        for question, answer in rows:
            print("=" * 60)
            print(f"问题 ID: {question.id}")
            print(f"问题序号: {question.question_index}")
            print(f"问题类型: {question.question_type}")
            print(f"问题: {question.question}")

            if answer:
                print(f"回答 ID: {answer.id}")
                print(f"回答: {answer.answer}")
            else:
                print("回答: 暂无")


if __name__ == "__main__":
    asyncio.run(main())