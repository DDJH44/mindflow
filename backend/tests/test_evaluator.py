import asyncio

from app.services.interview.evaluator import InterviewEvaluator


async def main():
    evaluator = InterviewEvaluator()

    interview_content = """
【问题 1】

请结合 MindFlow AI 面试助手项目，具体讲一下你从简历和项目经历数据入库、
向量化与 Milvus 检索，到生成个性化面试问题的完整实现链路。
你在其中负责了哪些部分，为什么选择 FastAPI、PostgreSQL、Redis 和 Milvus？

【候选人回答】

我主要负责后端部分。

用户上传简历之后，后端使用 FastAPI 接收文件，
然后把解析后的内容保存到 PostgreSQL。

之后把文档切分成 Chunk，通过 Embedding 模型转换成向量，
再写入 Milvus。

用户进行面试的时候，根据当前的问题生成向量，
然后去 Milvus 做相似度检索，再从 PostgreSQL 找回原始文本，
最后把这些内容提供给大模型生成个性化面试问题。

Redis 主要用于缓存一些高频数据。

【追问】

你提到 Milvus 相似度检索后会回 PostgreSQL 取原始文本。
能具体说一下 Milvus 里每条向量记录包含哪些字段，
以及你是通过什么 ID 或业务主键与 PostgreSQL 中的 Chunk 记录做关联的吗？

如果同一份简历被切成了多个 Chunk，
检索到多个向量后，你如何确保取回并拼接的是对应片段的原文？

【候选人回答】

Milvus 里面保存向量以及 document_id、project_id 等字段。
检索得到 Chunk 的 ID 后，再根据 Chunk ID 去 PostgreSQL 查询对应的文本。

多个 Chunk 会按照检索结果分别取回，
然后交给后续的大模型生成问题。
""".strip()

    result = await evaluator.evaluate(
        interview_content=interview_content,
    )

    print()
    print("========== INTERVIEW EVALUATION ==========")
    print()
    print(f"overall_score: {result.overall_score}")
    print(f"technical_score: {result.technical_score}")
    print(f"project_score: {result.project_score}")
    print(f"communication_score: {result.communication_score}")
    print()
    print("strengths:")
    for item in result.strengths:
        print(f"- {item}")

    print()
    print("weaknesses:")
    for item in result.weaknesses:
        print(f"- {item}")

    print()
    print("suggestions:")
    for item in result.suggestions:
        print(f"- {item}")

    print()
    print("feedback:")
    print(result.feedback)

    print()
    print("===========================================")


if __name__ == "__main__":
    asyncio.run(main())