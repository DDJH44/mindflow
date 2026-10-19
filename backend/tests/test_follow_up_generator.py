import asyncio

from app.schemas.interview.answer_analysis import AnswerAnalysis
from app.services.interview.follow_up_generator import (
    InterviewFollowUpGenerator,
)


async def main():
    generator = InterviewFollowUpGenerator()

    question = """
请结合 MindFlow AI 面试助手项目，具体讲一下你从简历/项目经历数据入库、
向量化与 Milvus 检索，到通过 FastAPI 调用并生成个性化面试问题的完整实现链路。
""".strip()

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

    analysis = AnswerAnalysis(
        answer_quality=68,
        technical_depth=45,
        completeness=60,
        strengths=[
            "按流程说明了文件上传、入库、向量化和检索流程",
            "提到了 FastAPI、PostgreSQL、Milvus 和 Redis",
            "说明了向量检索后回 PostgreSQL 获取原始文本",
        ],
        missing_points=[
            "未说明 Chunk 的具体切分策略",
            "未说明 Embedding 模型和向量维度",
            "未说明 Milvus 的索引类型和相似度度量方式",
            "未说明 PostgreSQL 与 Milvus 如何关联",
            "未说明 Prompt 如何实现个性化面试问题",
        ],
        summary="候选人能够描述完整技术链路，但技术实现细节不足。",
    )

    result = await generator.generate_follow_up(
        question=question,
        answer=answer,
        analysis=analysis,
    )

    print("========== FOLLOW-UP QUESTION ==========")
    print(result)
    print("========================================")


if __name__ == "__main__":
    asyncio.run(main())