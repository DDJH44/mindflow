import asyncio

from app.database.session import AsyncSessionLocal
from app.services.interview.interview_turn_service import (
    InterviewTurnService,
)


async def main():

    async with AsyncSessionLocal() as db:

        service = InterviewTurnService(db)

        answer = """
我主要负责后端部分。

用户上传简历之后，后端使用 FastAPI 接收文件，
然后把解析后的内容保存到 PostgreSQL。

之后把文档切分成 Chunk，通过 Embedding 模型转换成向量，
再写入 Milvus。

用户进行面试的时候，根据当前的问题生成向量，
然后去 Milvus 做相似度检索，再从 PostgreSQL 找回原始文本，
最后把这些内容提供给大模型生成个性化面试问题。

Redis 主要用于缓存一些高频数据。
""".strip()

        result = await service.process_answer(
            question_id=2,
            answer=answer,
        )

        saved_answer = result["answer"]
        analysis = result["analysis"]
        follow_up = result["follow_up_question"]

        print()
        print("========== INTERVIEW TURN ==========")

        print()
        print("【回答】")
        print(saved_answer.answer)

        print()
        print("【回答分析】")
        print(f"answer_quality: {analysis.answer_quality}")
        print(f"technical_depth: {analysis.technical_depth}")
        print(f"completeness: {analysis.completeness}")
        print(f"strengths: {analysis.strengths}")
        print(f"missing_points: {analysis.missing_points}")
        print(f"summary: {analysis.summary}")

        print()
        print("【追问】")
        print(f"question_id: {follow_up.id}")
        print(f"question_type: {follow_up.question_type}")
        print(f"question_index: {follow_up.question_index}")
        print(f"question: {follow_up.question}")

        print()
        print("====================================")


if __name__ == "__main__":
    asyncio.run(main())