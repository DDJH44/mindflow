"""知识图谱落库的归一化合并。

## 它防的是什么（2026-10-20 抽取实测发现）

同一实体在不同片段里的**写法**不一致 ——
「AI 面试助手」/「AI面试助手」（空格差异）、
「Milvus」被归为 other 与 technology（类型漂移）。

旧合并键是 (原名, 类型) 精确匹配，于是同一实体变成多个节点，
图是碎的 —— 而碎图不会报错，只会在展示与检索扩展时
表现出"同一个东西出现好几次"。

## 它验证

- 归一化键：空格差异、大小写差异 → 同一键
- 跨写法的实体落库后是**一个**节点
- 类型漂移时先到先得（一个节点一个类型）
- 关系引用跨写法也能挂到同一节点
- 幂等：重复落库不产生重复

用法：uv run python -m tests.test_knowledge_repository
"""

import sys

from sqlalchemy import delete, insert, select, text

from app.database.session import AsyncSessionLocal
from app.models.document import Document
from app.models.document_chunk import DocumentChunk
from app.models.knowledge_graph import (
    KnowledgeEntity,
    KnowledgeRelation,
)
from app.models.project import Project
from app.repositories.knowledge_repository import (
    KnowledgeRepository,
    _entity_key,
)
from app.services.knowledge.extraction_service import (
    ExtractedEntity,
    ExtractedRelation,
    ExtractionResult,
)


class Result:
    def __init__(self) -> None:
        self.rows: list[tuple[str, bool, str]] = []

    def check(self, label: str, ok: bool, detail: str = "") -> None:
        self.rows.append((label, ok, detail))
        mark = "OK  " if ok else "FAIL"
        suffix = f"  {detail}" if detail else ""
        print(f"  [{mark}] {label}{suffix}")

    @property
    def failed(self) -> list[str]:
        return [label for label, ok, _ in self.rows if not ok]


async def setup_sandbox() -> tuple[int, int, int]:
    """创建沙箱（__test__ 前缀，符合测试数据命名约定）。

    `knowledge_entities` 的 project_id 与 source_chunk_id 都有
    外键约束，沙箱必须是真实存在的行 —— 项目 + 文档 + 两个块，
    用后按依赖逆序删除。
    """

    async with AsyncSessionLocal() as db:
        project_id = int(
            (
                await db.execute(
                    insert(Project)
                    .values(
                        name="__test__知识图谱归一化", owner_id=3
                    )
                    .returning(Project.id)
                )
            ).scalar_one()
        )

        document_id = int(
            (
                await db.execute(
                    insert(Document)
                    .values(
                        name="归一化验证",
                        original_filename="__test__norm.txt",
                        file_type=".txt",
                        document_type="other",
                        project_id=project_id,
                        status="embedded",
                    )
                    .returning(Document.id)
                )
            ).scalar_one()
        )

        chunk_ids = list(
            (
                await db.execute(
                    insert(DocumentChunk)
                    .values(
                        [
                            {
                                "document_id": document_id,
                                "content": f"沙箱片段 {i}",
                                "chunk_index": i,
                            }
                            for i in range(2)
                        ]
                    )
                    .returning(DocumentChunk.id)
                )
            ).scalars()
        )

        await db.commit()

    return project_id, chunk_ids[0], chunk_ids[1]


async def teardown_sandbox(project_id: int) -> None:
    async with AsyncSessionLocal() as db:
        await db.execute(
            delete(KnowledgeRelation).where(
                KnowledgeRelation.project_id == project_id
            )
        )
        await db.execute(
            delete(KnowledgeEntity).where(
                KnowledgeEntity.project_id == project_id
            )
        )
        await db.execute(
            text(
                "DELETE FROM document_chunks WHERE document_id IN "
                "(SELECT id FROM documents WHERE project_id = :p)"
            ),
            {"p": project_id},
        )
        await db.execute(
            text("DELETE FROM documents WHERE project_id = :p"),
            {"p": project_id},
        )
        await db.execute(
            text("DELETE FROM projects WHERE id = :p"), {"p": project_id}
        )
        await db.commit()


async def count_entities(project_id: int) -> int:
    async with AsyncSessionLocal() as db:
        rows = (
            await db.execute(
                select(KnowledgeEntity).where(
                    KnowledgeEntity.project_id == project_id
                )
            )
        ).scalars().all()
        return len(rows)


async def main() -> int:
    sys.path.insert(0, r"D:\yuxi\MindFlow\backend")

    result = Result()

    print("=" * 78)
    print("知识图谱归一化合并验证")
    print("=" * 78)

    sandbox, chunk_a, chunk_b = await setup_sandbox()

    # ================================================
    print()
    print("① 归一化键：写法差异 → 同一键")
    # ================================================

    result.check(
        "空格差异合并（AI 面试助手 == AI面试助手）",
        _entity_key("AI 面试助手") == _entity_key("AI面试助手"),
    )
    result.check(
        "大小写差异合并（Milvus == milvus）",
        _entity_key("Milvus") == _entity_key("milvus"),
    )
    result.check(
        "不同实体不误合并（FastAPI != PostgreSQL）",
        _entity_key("FastAPI") != _entity_key("PostgreSQL"),
    )

    # ================================================
    print()
    print("② 跨写法落库 → 单节点；类型漂移先到先得")
    # ================================================

    async with AsyncSessionLocal() as db:
        repo = KnowledgeRepository(db)

        # chunk 101：建立「MindFlow AI 面试助手」（system）
        # 与「Milvus」（technology）
        await repo.store_chunk_extraction(
            project_id=sandbox,
            chunk_id=chunk_a,
            result=ExtractionResult(
                entities=[
                    ExtractedEntity(
                        name="MindFlow AI 面试助手",
                        entity_type="system",
                    ),
                    ExtractedEntity(
                        name="Milvus", entity_type="technology"
                    ),
                ],
                relations=[
                    ExtractedRelation(
                        subject="MindFlow AI 面试助手",
                        predicate="使用",
                        object="Milvus",
                    )
                ],
            ),
        )

        # chunk 102：同实体的**不同写法**与**漂移类型** ——
        # 「MindFlow AI面试助手」（去空格）+「milvus」（小写 + other）
        await repo.store_chunk_extraction(
            project_id=sandbox,
            chunk_id=chunk_b,
            result=ExtractionResult(
                entities=[
                    ExtractedEntity(
                        name="MindFlow AI面试助手",
                        entity_type="other",
                    ),
                    ExtractedEntity(
                        name="milvus", entity_type="other"
                    ),
                ],
                relations=[
                    ExtractedRelation(
                        subject="MindFlow AI面试助手",
                        predicate="存储向量于",
                        object="milvus",
                    )
                ],
            ),
        )

    total = await count_entities(sandbox)
    result.check(
        "四个抽取（两写法 × 2 实体）落库后只有 2 个节点",
        total == 2,
        f"{total} 个",
    )

    async with AsyncSessionLocal() as db:
        entities = (
            (
                await db.execute(
                    select(KnowledgeEntity).where(
                        KnowledgeEntity.project_id == sandbox
                    )
                )
            )
            .scalars()
            .all()
        )

        milvus = [e for e in entities if "milvus" in e.name.lower()]
        system = [
            e for e in entities if "mindflow" in e.name.lower()
        ]

        result.check(
            "Milvus 类型先到先得（technology，不被后来的 other 覆盖）",
            len(milvus) == 1 and milvus[0].entity_type == "technology",
            milvus[0].entity_type if milvus else "未找到",
        )
        result.check(
            "节点名保留第一次落库的写法",
            len(system) == 1
            and system[0].name == "MindFlow AI 面试助手",
            system[0].name if system else "未找到",
        )

        # ================================================
        print()
        print("③ 关系挂到同一节点 + 幂等")
        # ================================================

        relations = (
            (
                await db.execute(
                    select(KnowledgeRelation).where(
                        KnowledgeRelation.project_id == sandbox
                    )
                )
            )
            .scalars()
            .all()
        )

        result.check(
            "跨写法的关系（存储向量于）挂到了同一对节点",
            len(relations) == 2,
            f"{len(relations)} 条",
        )

    # 幂等：重复落库同一段不产生重复
    async with AsyncSessionLocal() as db:
        await KnowledgeRepository(db).store_chunk_extraction(
            project_id=sandbox,
            chunk_id=chunk_a,
            result=ExtractionResult(
                entities=[
                    ExtractedEntity(
                        name="MindFlow AI 面试助手",
                        entity_type="system",
                    ),
                    ExtractedEntity(
                        name="Milvus", entity_type="technology"
                    ),
                ],
                relations=[
                    ExtractedRelation(
                        subject="MindFlow AI 面试助手",
                        predicate="使用",
                        object="Milvus",
                    )
                ],
            ),
        )

    total_after_repeat = await count_entities(sandbox)
    result.check(
        "重复落库不产生新节点（幂等）",
        total_after_repeat == 2,
        f"{total_after_repeat} 个",
    )

    await teardown_sandbox(sandbox)

    print()
    print("=" * 78)
    passed = len(result.rows) - len(result.failed)
    print(f"通过 {passed} / {len(result.rows)}")

    if result.failed:
        print("失败项:")
        for label in result.failed:
            print(f"  - {label}")
        return 1

    print("全部通过")
    return 0


if __name__ == "__main__":
    import asyncio

    sys.exit(asyncio.run(main()))
