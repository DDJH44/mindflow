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
        existing = {
            (entity.name, entity.entity_type): entity
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
            key = (extracted.name, extracted.entity_type)
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

            # 名称 → 实体。同一名称可能有不同种类
            # （例如"Go"既是语言也是动词），按名称索引时
            # 取第一次出现的即可 —— 关系里只有名称，
            # 无法区分种类，这是这套数据模型的已知局限。
            entity_by_name.setdefault(extracted.name, entity)

        # ---------------- 关系 ----------------
        created_relations = 0

        for relation in result.relations:
            subject = entity_by_name.get(relation.subject)
            obj = entity_by_name.get(relation.object)

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
