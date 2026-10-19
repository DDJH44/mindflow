import asyncio

from app.database.session import AsyncSessionLocal
from app.services.interview_session_service import InterviewSessionService


async def main():
    async with AsyncSessionLocal() as db:
        service = InterviewSessionService(db)

        session = await service.create_session(
            project_id=3,
            # 会话必须记录所有者（§5.4）。
            # 该项目属于用户 3，service 会核对一致性。
            user_id=3,
            interview_type="technical",
        )

        print("========== 创建测试面试 Session 成功 ==========")
        print(f"session_id: {session.id}")
        print(f"user_id: {session.user_id}")
        print(f"project_id: {session.project_id}")
        print(f"status: {session.status}")
        print(f"interview_type: {session.interview_type}")
        print(f"max_questions: {session.max_questions}")
        print(f"questions_asked: {session.questions_asked}")
        print(f"current_question_index: {session.current_question_index}")
        print("==============================================")


if __name__ == "__main__":
    asyncio.run(main())
