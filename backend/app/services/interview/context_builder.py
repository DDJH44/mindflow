from sqlalchemy.ext.asyncio import AsyncSession

from app.services.retrieval_service import RetrievalService


class InterviewContextBuilder:
    """构建面试所需的候选人上下文"""

    DOCUMENT_TYPES = [
        "resume",
        "jd",
        "project",
        "code",
    ]

    def __init__(self, db: AsyncSession):
        self.retrieval_service = RetrievalService(db)

    async def build_context(
        self,
        project_id: int,
        query: str,
        limit_per_type: int = 3,
    ) -> dict:
        """
        根据项目和问题，检索不同类型的候选人资料。

        返回 {document_type: [检索结果]}。
        每个检索结果保留 `chunk_id`，供上层记录资料依据
        （`InterviewQuestion.evidence_chunk_ids`）。
        """

        context = {}

        for document_type in self.DOCUMENT_TYPES:
            results = await self.retrieval_service.retrieve(
                query=query,
                limit=limit_per_type,
                project_id=project_id,
                document_type=document_type,
            )

            context[document_type] = results

        return context

    @staticmethod
    def collect_evidence_chunk_ids(
        context: dict,
    ) -> list[int]:
        """按"喂给模型的上下文顺序"提取 chunk_id 列表。

        顺序必须与 `_format_context` 的遍历顺序完全一致：
        那边的顺序决定了模型看到证据的次序，
        这里的顺序决定落库的证据次序。
        两者不一致会让"题目的依据"与"实际使用的依据"对不上，
        排查时会误导人。

        因此两个方法都按 `DOCUMENT_TYPES` 的顺序、
        每个类型内部按检索结果的原顺序遍历。

        去重但**保持首次出现的位置**：
        同一个 chunk 可能同时被多种类型召回（例如项目说明
        与代码片段内容重叠），重复记录会让证据列表失真。
        """

        seen: set[int] = set()
        chunk_ids: list[int] = []

        for document_type in InterviewContextBuilder.DOCUMENT_TYPES:
            results = context.get(document_type) or []

            for item in results:
                chunk_id = item.get("chunk_id")

                if chunk_id is None or chunk_id in seen:
                    continue

                seen.add(chunk_id)
                chunk_ids.append(chunk_id)

        return chunk_ids
