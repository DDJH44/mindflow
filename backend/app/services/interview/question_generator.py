from app.services.interview.context_builder import InterviewContextBuilder
from app.services.openai_llm_service import OpenAILLMService

class InterviewQuestionGenerator:
    """根据候选人上下文生成个性化面试问题"""

    SYSTEM_PROMPT = """
你是一名专业的技术面试官。

你的任务是根据候选人的真实资料和目标岗位信息，
生成一个与候选人经历高度相关的面试问题。

要求：

1. 问题必须基于提供的候选人资料。
2. 不要凭空编造候选人没有提供的经历。
3. 优先询问候选人的实际项目、技术选择、实现细节和问题解决过程。
4. 问题应该能够进一步验证候选人的真实技术能力。
5. 不要一次生成多个问题。
6. 不要解释你的分析过程。
7. 只返回最终的面试问题。
""".strip()

    def __init__(self, db):
        self.context_builder = InterviewContextBuilder(db)
        self.llm_service = OpenAILLMService()

    @staticmethod
    def _format_context(context: dict) -> str:
        """把检索结果格式化为喂给模型的上下文。

        **必须与 `InterviewContextBuilder.collect_evidence_chunk_ids`
        按同一顺序遍历**（即 DOCUMENT_TYPES 的固定顺序）。

        为什么不能直接用 `context.items()`：
        那样得到的是 dict 插入顺序。当前 `build_context` 恰好
        按 DOCUMENT_TYPES 顺序构建，所以两者一致 —— 但这是**巧合**，
        一旦 build_context 改了构建顺序，落库的 evidence 顺序
        就会与模型实际看到的顺序不符，而问题不会立刻暴露。
        这里显式遍历 DOCUMENT_TYPES，把顺序变成强约束。
        """

        sections = []

        for document_type in InterviewContextBuilder.DOCUMENT_TYPES:
            results = context.get(document_type)

            if not results:
                continue

            sections.append(
                f"【{document_type}】"
            )

            for index, item in enumerate(results, start=1):
                sections.append(
                    f"{index}. {item['content']}"
                )

        if not sections:
            return "当前没有检索到候选人相关资料。"

        return "\n".join(sections)

    async def generate_question(
        self,
        project_id: int,
        query: str,
    ) -> str:
        """
        根据项目上下文生成一个面试问题。
        """

        result = await self.generate_question_with_evidence(
            project_id=project_id,
            query=query,
        )

        return result["question"]

    async def generate_question_with_evidence(
        self,
        project_id: int,
        query: str,
    ) -> dict:
        """生成问题，并返回它依据的资料 Chunk。

        返回：
            {
                "question": str,
                "evidence_chunk_ids": list[int],
                "is_general": bool,
            }

        为什么要把证据一起返回：
        `InterviewQuestion.evidence_chunk_ids` 要求"资料型问题
        必须包含可追溯依据"（MIND_FLOW_PLAN.md §9.3）。
        证据必须在**检索发生的那一刻**捕获 ——
        事后再去检索一次得到的是另一批 chunk，无法证明
        当初用的是哪些。因此这里把检索结果直接带出来。

        没有检索到任何资料时判定为通用能力题（is_general=True），
        由上层显式标记，而不是假装它是资料题。
        """

        context = await self.context_builder.build_context(
            project_id=project_id,
            query=query,
        )

        formatted_context = self._format_context(context)

        evidence_chunk_ids = (
            self.context_builder.collect_evidence_chunk_ids(context)
        )

        has_evidence = bool(evidence_chunk_ids)

        if has_evidence:
            evidence_instruction = (
                "请生成一个能够深入了解候选人真实经历"
                "和技术能力的问题。"
            )
        else:
            # 没有资料时明确告诉模型，避免它编造候选人经历。
            evidence_instruction = (
                "当前没有检索到候选人资料。"
                "请生成一个不依赖具体资料、可考察通用技术能力的问题，"
                "不要编造候选人的项目或经历。"
            )

        prompt = f"""
请根据以下候选人资料生成一个面试问题。

候选人资料：

{formatted_context}

当前面试方向：

{query}

{evidence_instruction}
只返回一个最终问题。
""".strip()

        question = await self.llm_service.generate(
            prompt=prompt,
            system_prompt=self.SYSTEM_PROMPT,
            temperature=0.5,
            max_tokens=1000,
        )

        return {
            "question": question,
            "evidence_chunk_ids": evidence_chunk_ids,
            "is_general": not has_evidence,
        }