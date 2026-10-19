import asyncio

from app.services.interview.answer_analyzer import (
    InterviewAnswerAnalyzer,
)


async def main():
    analyzer = InterviewAnswerAnalyzer()

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

    result = await analyzer.analyze(
        question=question,
        answer=answer,
    )

    print("========== 回答分析成功 ==========")
    print(f"answer_quality: {result.answer_quality}")
    print(f"technical_depth: {result.technical_depth}")
    print(f"completeness: {result.completeness}")
    print(f"strengths: {result.strengths}")
    print(f"missing_points: {result.missing_points}")
    print(f"summary: {result.summary}")
    print("==================================")


if __name__ == "__main__":
    asyncio.run(main())