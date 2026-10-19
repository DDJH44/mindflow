import asyncio

from app.database.session import AsyncSessionLocal
from app.services.interview_answer_service import (
    InterviewAnswerService,
)


async def main():
    async with AsyncSessionLocal() as db:

        service = InterviewAnswerService(db)

        answer = await service.create_answer(
            question_id=2,
            answer="""
我主要负责后端部分。

用户上传简历之后，后端使用 FastAPI 接收文件，
然后把解析后的内容保存到 PostgreSQL。

之后把文档切分成 Chunk，通过 Embedding 模型转换成向量，
再写入 Milvus。

用户进行面试的时候，根据当前的问题生成向量，
然后去 Milvus 做相似度检索，再从 PostgreSQL 找回原始文本，
最后把这些内容提供给大模型生成个性化面试问题。

Redis 主要用于缓存一些高频数据。
""".strip(),
        )

        print("========== ANSWER SAVED ==========")
        print(f"answer_id: {answer.id}")
        print(f"question_id: {answer.question_id}")
        print(f"answer: {answer.answer}")
        print("==================================")


if __name__ == "__main__":
    asyncio.run(main())