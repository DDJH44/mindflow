import asyncio

from app.database.session import AsyncSessionLocal
from app.services.interview.interview_engine_service import (
    InterviewEngineService,
)
from app.services.interview.interview_turn_service import InterviewTurnService
from app.services.interview.evaluation_service import (
    InterviewEvaluationService,
)


async def main():
    async with AsyncSessionLocal() as db:
        # ============================================================
        # 1. 生成第一道面试题
        # ============================================================
        #
        # ⚠️ 前置条件：session_id 指向的会话必须处于 planned 或 asking。
        #
        # 本脚本早期用硬编码的会话 id，那些会话后来停在 created
        # （迁移前的旧状态，等价于 draft），因此直接跑会被状态机拒绝 ——
        # 这是**正确行为**：draft 会话还没准备上下文，不该直接出题。
        # 若要重跑，请先用 InterviewFlowService.start_interview
        # 或 API 的 POST /api/interviews/{id}/start 把会话推进。
        #
        # 索引不再由调用方传入：服务端按会话的
        # current_question_index 自动推导，避免人工约定导致错乱。

        engine = InterviewEngineService(db)

        question = await engine.generate_and_save_question(
            session_id=2,
            project_id=3,
            query="考察候选人的后端开发能力和项目实践经验",
            question_type="technical",
        )

        print("=" * 60)
        print("第一道面试题")
        print("=" * 60)
        print(f"question_id: {question.id}")
        print(f"question: {question.question}")

        # ============================================================
        # 2. 模拟候选人回答
        # ============================================================

        answer_text = """
我主要负责 MindFlow AI 面试助手的后端开发。

用户上传简历以后，FastAPI 接收文件并进行解析，
文档内容保存到 PostgreSQL，然后进行 Chunk 切分。

Chunk 会通过 Embedding 模型转换成向量，
再写入 Milvus。

用户进入面试以后，系统根据当前问题进行向量检索，
从 Milvus 获取相关 Chunk，
然后根据 Chunk ID 回 PostgreSQL 获取原始内容，
最后将这些上下文交给大模型生成个性化面试问题。

我主要负责文档上传、Chunk 切分、Embedding、
Milvus 检索以及面试相关后端接口。
""".strip()

        # ============================================================
        # 3. 分析候选人回答 + 生成 Follow-up
        # ============================================================

        turn_service = InterviewTurnService(db)

        turn_result = await turn_service.process_answer(
            question_id=question.id,
            answer=answer_text,
        )

        analysis = turn_result["analysis"]
        follow_up = turn_result["follow_up_question"]

        print("=" * 60)
        print("第一轮回答分析")
        print("=" * 60)
        print(f"answer_quality: {analysis.answer_quality}")
        print(f"technical_depth: {analysis.technical_depth}")
        print(f"completeness: {analysis.completeness}")

        print("\nstrengths:")
        for item in analysis.strengths:
            print(f"- {item}")

        print("\nmissing_points:")
        for item in analysis.missing_points:
            print(f"- {item}")

        print(f"\nsummary: {analysis.summary}")

        print("=" * 60)
        print("Follow-up 问题")
        print("=" * 60)
        print(f"question_id: {follow_up.id}")
        print(f"question_type: {follow_up.question_type}")
        print(f"question_index: {follow_up.question_index}")
        print(f"question: {follow_up.question}")

        # ============================================================
        # 4. 模拟回答 Follow-up
        # ============================================================

        follow_up_answer = """
Milvus 中每条向量记录会保存 chunk 对应的 ID、
document_id、project_id、document_type 以及向量本身。

其中 chunk ID 和 PostgreSQL 中的 DocumentChunk ID 对应。

检索得到多个 Chunk 后，我会根据检索结果中的 chunk ID
批量查询 PostgreSQL，然后按照检索结果的相关性进行排序，
最后将多个 Chunk 的内容组合成上下文，再交给大模型。

这样可以保证 Milvus 中检索到的向量和 PostgreSQL
中的原始 Chunk 能够正确对应。
""".strip()

        second_turn = await turn_service.process_answer(
            question_id=follow_up.id,
            answer=follow_up_answer,
        )

        second_analysis = second_turn["analysis"]
        second_follow_up = second_turn["follow_up_question"]

        print("=" * 60)
        print("第二轮回答分析")
        print("=" * 60)
        print(f"answer_quality: {second_analysis.answer_quality}")
        print(f"technical_depth: {second_analysis.technical_depth}")
        print(f"completeness: {second_analysis.completeness}")

        print("\nstrengths:")
        for item in second_analysis.strengths:
            print(f"- {item}")

        print("\nmissing_points:")
        for item in second_analysis.missing_points:
            print(f"- {item}")

        print(f"\nsummary: {second_analysis.summary}")

        print("=" * 60)
        print("第二轮 Follow-up")
        print("=" * 60)
        print(f"question_id: {second_follow_up.id}")
        print(f"question_type: {second_follow_up.question_type}")
        print(f"question_index: {second_follow_up.question_index}")
        print(f"question: {second_follow_up.question}")

        # ============================================================
        # 5. 最终面试评价
        # ============================================================

        evaluation_service = InterviewEvaluationService(db)

        evaluation_result = await evaluation_service.evaluate_session(
            session_id=2
        )

        evaluation = evaluation_result["evaluation"]
        saved_evaluation = evaluation_result["saved_evaluation"]

        print("=" * 60)
        print("最终面试评价")
        print("=" * 60)
        print(f"evaluation_id: {saved_evaluation.id}")
        print(f"overall_score: {evaluation.overall_score}")
        print(f"technical_score: {evaluation.technical_score}")
        print(f"project_score: {evaluation.project_score}")
        print(f"communication_score: {evaluation.communication_score}")

        print("\nstrengths:")
        for item in evaluation.strengths:
            print(f"- {item}")

        print("\nweaknesses:")
        for item in evaluation.weaknesses:
            print(f"- {item}")

        print("\nsuggestions:")
        for item in evaluation.suggestions:
            print(f"- {item}")

        print(f"\nfeedback:\n{evaluation.feedback}")


if __name__ == "__main__":
    asyncio.run(main())