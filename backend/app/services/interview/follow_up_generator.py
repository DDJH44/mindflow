from app.schemas.interview.answer_analysis import AnswerAnalysis
from app.services.openai_llm_service import OpenAILLMService


class InterviewFollowUpGenerator:
    """根据候选人回答分析结果生成追问"""

    SYSTEM_PROMPT = """
你是一名专业的技术面试官。

你的任务是根据：
1. 原始面试问题
2. 候选人的回答
3. 对候选人回答的分析结果

生成一个有针对性的技术追问。

要求：

1. 追问必须基于候选人刚才实际说过的内容。
2. 优先针对 missing_points 中最重要、最值得验证的技术点进行追问。
3. 不要凭空引入候选人没有提到的技术经历。
4. 不要重复原来的问题。
5. 追问应该能够进一步验证候选人的真实技术能力。
6. 追问应该具体，而不是泛泛地问“请详细介绍一下”。
7. 一次只能生成一个问题。
8. 只返回最终的面试问题，不要解释分析过程。
""".strip()

    def __init__(self):
        self.llm_service = OpenAILLMService()

    async def generate_follow_up(
        self,
        question: str,
        answer: str,
        analysis: AnswerAnalysis,
        seed: int | None = None,
    ) -> str:

        if not question.strip():
            raise ValueError("原面试问题不能为空")

        if not answer.strip():
            raise ValueError("候选人回答不能为空")

        missing_points = "\n".join(
            f"- {item}"
            for item in analysis.missing_points
        )

        strengths = "\n".join(
            f"- {item}"
            for item in analysis.strengths
        )

        prompt = f"""
请根据下面的信息生成一个技术面试追问。

【原始面试问题】

{question}

【候选人回答】

{answer}

【回答质量】

{analysis.answer_quality}

【技术深度】

{analysis.technical_depth}

【完整度】

{analysis.completeness}

【候选人回答中的优点】

{strengths}

【回答中缺失或需要进一步确认的信息】

{missing_points}

请从上述 missing_points 中选择一个最值得继续验证的技术点，
生成一个具体的追问。

只返回一个最终问题。
""".strip()

        return await self.llm_service.generate(
            prompt=prompt,
            system_prompt=self.SYSTEM_PROMPT,
            temperature=0.4,
            max_tokens=2000,
            seed=seed,
        )