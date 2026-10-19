import asyncio

from app.database.session import AsyncSessionLocal
from app.services.interview.evaluation_service import InterviewEvaluationService


async def main():
    async with AsyncSessionLocal() as db:
        service = InterviewEvaluationService(db)

        result = await service.evaluate_session(
            session_id=1
        )

        evaluation = result["evaluation"]
        saved_evaluation = result["saved_evaluation"]

        print("=" * 60)
        print("LLM 评价结果")
        print("=" * 60)
        print(f"综合评分: {evaluation.overall_score}")
        print(f"技术能力: {evaluation.technical_score}")
        print(f"项目能力: {evaluation.project_score}")
        print(f"沟通表达: {evaluation.communication_score}")

        print("=" * 60)
        print("数据库保存结果")
        print("=" * 60)
        print(f"Evaluation ID: {saved_evaluation.id}")
        print(f"Session ID: {saved_evaluation.session_id}")
        print(f"综合评分: {saved_evaluation.overall_score}")
        print(f"技术能力: {saved_evaluation.technical_score}")
        print(f"项目能力: {saved_evaluation.project_score}")
        print(f"沟通表达: {saved_evaluation.communication_score}")
        print(f"Feedback: {saved_evaluation.feedback}")


if __name__ == "__main__":
    asyncio.run(main())