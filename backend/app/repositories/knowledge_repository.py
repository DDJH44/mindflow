"""知识图谱的读写（§30.3）。

`store_chunk_extraction` 把一段材料片段的抽取结果落库，
并保证**幂等**：重复跑同一段不会产生重复实体或关系。

为什么必须幂等：抽取会失败并被重试（上游抖动、模型偶尔
返回无法解析的 JSON）。不幂等的话，一次重试就会让图谱里
出现重复节点，而重复节点会让"关系扩展"带回重复的片段 ——
表现为检索结果里有重复内容，却看不出原因。
"""

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.logging import get_logger
from app.models.knowledge_graph import (
    KnowledgeEntity,
    KnowledgeRelation,
)
from app.services.knowledge.extraction_service import ExtractionResult

logger = get_logger("app.knowledge_repository")


def _entity_key(name: str) -> str:
    """实体的合并键：移除全部空白并小写。

    为什么不直接用原名精确匹配（2026-10-20 抽取实测）：

    同一实体在不同片段里的**写法**会不一致 ——

    - 「AI 面试助手」vs「AI面试助手」（空格差异）
    - 「Milvus」在片段 A 被归为 other、片段 B 被归为 technology

    精确匹配会让它们变成多个节点，图就碎了。

    归一化只解决写法差异；「MindFlow」与「MindFlow AI 面试助手」
    这类**指称**差异仍在 —— 那需要别名机制，留给关系扩展检索
    （§30.3 第 2 步）一并考虑，现在不预设。

    类型不一致时**先到先得**：图里一个节点一个类型，
    后来的同名人不覆盖 —— 覆盖反而需要一套"哪个类型更对"的
    规则，而那没有可靠依据。
    """
    return "".join(name.split()).casefold()


class KnowledgeRepository:
    def __init__(self, session: AsyncSession):
        self.session = session

    async def store_chunk_extraction(
        self,
        project_id: int,
        chunk_id: int,
        result: ExtractionResult,
    ) -> tuple[int, int]:
        """落库一段材料片段的抽取结果，返回 (实体数, 关系数)。

        幂等：已存在的实体/关系不会被重复插入。
        """

        if not result.entities and not result.relations:
            return 0, 0

        # ---------------- 实体 ----------------
        #
        # 先查已有的，再插入缺的。
        #
        # 不用 `ON CONFLICT DO NOTHING` 是因为还需要拿到
        # **已存在实体的 id** 来建关系 —— 冲突时 RETURNING
        # 不返回任何行，于是还要再查一次，反而更绕。
        #
        # 合并键是**归一化名**（`_entity_key`），不含类型 ——
        # 类型不一致时先到先得（见该函数的说明）。
        existing = {
            _entity_key(entity.name): entity
            for entity in (
                await self.session.execute(
                    select(KnowledgeEntity).where(
                        KnowledgeEntity.project_id == project_id
                    )
                )
            ).scalars().all()
        }

        entity_by_name: dict[str, KnowledgeEntity] = {}

        created_entities = 0

        for extracted in result.entities:
            key = _entity_key(extracted.name)
            entity = existing.get(key)

            if entity is None:
                entity = KnowledgeEntity(
                    project_id=project_id,
                    name=extracted.name,
                    entity_type=extracted.entity_type,
                    source_chunk_id=chunk_id,
                )
                self.session.add(entity)
                await self.session.flush()

                existing[key] = entity
                created_entities += 1

            # 归一化名 → 实体。
            # 同名不同种（"Go"既是语言也是动词）按先到先得 ——
            # 关系里只有名称，无法区分种类，
            # 这是这套数据模型的已知局限。
            entity_by_name.setdefault(key, entity)

        # ---------------- 关系 ----------------
        created_relations = 0

        for relation in result.relations:
            # 关系里的名称同样按归一化键查找 ——
            # 「使用 AI 面试助手」与「使用 AI面试助手」
            # 应指向同一个节点。
            subject = entity_by_name.get(
                _entity_key(relation.subject)
            )
            obj = entity_by_name.get(_entity_key(relation.object))

            if subject is None or obj is None:
                # `_sanitize` 已经过滤过一次，这里再防一手：
                # 同一名称映射到不同种类时可能仍然取不到。
                continue

            duplicate = (
                await self.session.execute(
                    select(KnowledgeRelation.id).where(
                        KnowledgeRelation.project_id == project_id,
                        KnowledgeRelation.subject_id == subject.id,
                        KnowledgeRelation.predicate
                        == relation.predicate,
                        KnowledgeRelation.object_id == obj.id,
                    )
                )
            ).first()

            if duplicate is not None:
                continue

            self.session.add(
                KnowledgeRelation(
                    project_id=project_id,
                    subject_id=subject.id,
                    predicate=relation.predicate,
                    object_id=obj.id,
                    source_chunk_id=chunk_id,
                )
            )
            created_relations += 1

        await self.session.commit()

        return created_entities, created_relations

    async def count_for_project(
        self, project_id: int
    ) -> tuple[int, int]:
        """返回 (实体数, 关系数)。供验证脚本与报告使用。"""

        from sqlalchemy import func

        entities = (
            await self.session.execute(
                select(func.count(KnowledgeEntity.id)).where(
                    KnowledgeEntity.project_id == project_id
                )
            )
        ).scalar_one()

        relations = (
            await self.session.execute(
                select(func.count(KnowledgeRelation.id)).where(
                    KnowledgeRelation.project_id == project_id
                )
            )
        ).scalar_one()

        return int(entities), int(relations)

    async def get_graph(
        self, project_id: int
    ) -> tuple[list[KnowledgeEntity], list[KnowledgeRelation]]:
        """返回项目级的 (实体, 关系)，供展示端点使用。

        不在 SQL 里算 degree：项目级图谱只有几十个节点，
        在 Python 里数一次比写 join + group by 清楚 ——
        后者要同时按 subject 和 object 两列聚合，可读性差。
        """

        entities = (
            (
                await self.session.execute(
                    select(KnowledgeEntity).where(
                        KnowledgeEntity.project_id == project_id
                    )
                )
            )
            .scalars()
            .all()
        )

        relations = (
            (
                await self.session.execute(
                    select(KnowledgeRelation).where(
                        KnowledgeRelation.project_id == project_id
                    )
                )
            )
            .scalars()
            .all()
        )

        return list(entities), list(relations)
