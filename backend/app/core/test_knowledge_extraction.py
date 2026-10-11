"""验证知识图谱的抽取与落库（§30.3 第 1 步）。

## 这个套件要防什么

抽取是图谱的地基：抽错了，后面所有环节都在错误基础上工作，
而那种错误**不会报错** —— 只会让检索带回不相干的片段。
因此这里既验证"能抽出来"，也验证"脏数据被清掉"。

## 为什么脏数据校验要单独测

模型确实会犯这些错（下面每一条都有对应的构造输入）：
- 关系引用一个没在实体列表里的名词 → 指向不存在节点的边
- 自环（"PostgreSQL 使用 PostgreSQL"）→ 无意义的边
- 种类不在允许集合里 → 分类不可靠
- 重复实体/关系 → 重复节点让关联带回重复片段

**这些都不需要调模型就能测** —— `_sanitize` 是纯函数，
直接喂构造好的输入即可。只有"能抽出来"那一节需要真模型。

用法：uv run python -m app.core.test_knowledge_extraction
"""

import asyncio
import sys

from sqlalchemy import text

from app.database.session import AsyncSessionLocal

results: list[tuple[str, bool]] = []


def record(label: str, ok: bool, extra: str = "") -> None:
    results.append((label, ok))
    suffix = f"  {extra}" if extra else ""
    print(f"  [{'OK  ' if ok else 'FAIL'}] {label}{suffix}")


async def main() -> int:
    sys.path.insert(0, r"D:\yuxi\MindFlow\backend")

    from app.repositories.knowledge_repository import (
        KnowledgeRepository,
    )
    from app.services.knowledge.extraction_service import (
        ENTITY_TYPES,
        ExtractedEntity,
        ExtractedRelation,
        ExtractionResult,
        KnowledgeExtractionService,
    )

    # ================================================
    print()
    print("=" * 74)
    print("1. 脏数据清理（纯函数，不调模型）")
    print("=" * 74)

    dirty = ExtractionResult(
        entities=[
            ExtractedEntity(name="PostgreSQL", entity_type="technology"),
            # 重复
            ExtractedEntity(name="PostgreSQL", entity_type="technology"),
            # 空名称
            ExtractedEntity(name="   ", entity_type="technology"),
            # 种类不在允许集合里
            ExtractedEntity(name="某个新框架", entity_type="framework"),
        ],
        relations=[
            # 引用不存在的实体
            ExtractedRelation(
                subject="PostgreSQL",
                predicate="替代",
                object="不存在的实体",
            ),
            # 自环
            ExtractedRelation(
                subject="PostgreSQL",
                predicate="使用",
                object="PostgreSQL",
            ),
            # 正常
            ExtractedRelation(
                subject="某个新框架",
                predicate="使用",
                object="PostgreSQL",
            ),
            # 重复
            ExtractedRelation(
                subject="某个新框架",
                predicate="使用",
                object="PostgreSQL",
            ),
        ],
    )

    clean = KnowledgeExtractionService._sanitize(dirty)

    record(
        "去掉了重复实体与空名称",
        len(clean.entities) == 2,
        f"剩 {len(clean.entities)} 个：{[e.name for e in clean.entities]}",
    )
    record(
        "未知种类归为 other（不是丢弃，也不是硬塞成 skill）",
        any(
            e.name == "某个新框架" and e.entity_type == "other"
            for e in clean.entities
        ),
        f"种类={[e.entity_type for e in clean.entities]}",
    )
    record(
        "丢弃了引用不存在实体的关系",
        all(
            r.object != "不存在的实体" for r in clean.relations
        ),
    )
    record(
        "丢弃了自环",
        all(r.subject != r.object for r in clean.relations),
    )
    record(
        "去掉了重复关系",
        len(clean.relations) == 1,
        f"剩 {len(clean.relations)} 条",
    )

    # 兜底不能是 skill —— 第一版因此把候选人姓名归成了"技能"
    record(
        "兜底种类不是 skill（实测踩过这个坑）",
        "other" in ENTITY_TYPES and "person" in ENTITY_TYPES,
        f"种类集合含 person 与 other",
    )

    # ================================================
    print()
    print("=" * 74)
    print("2. 真实材料抽取（调模型）")
    print("=" * 74)

    sample = (
        "张明是后端开发工程师，主力语言是 Python。\n"
        "他在 MindFlow 项目里负责后端，用 FastAPI 构建服务，\n"
        "用 PostgreSQL 存数据，用 Milvus 存向量。"
    )

    try:
        extracted = await KnowledgeExtractionService().extract(sample)
    except Exception as exc:  # noqa: BLE001
        record(
            f"抽取调用失败：{type(exc).__name__}",
            False,
            str(exc)[:80],
        )
        extracted = None

    if extracted is not None:
        record(
            "抽出了实体",
            len(extracted.entities) >= 4,
            f"{len(extracted.entities)} 个："
            f"{[e.name for e in extracted.entities][:6]}",
        )
        record(
            "抽出了关系",
            len(extracted.relations) >= 2,
            f"{len(extracted.relations)} 条",
        )

        # 人名必须被识别为 person 而不是技能
        person_ok = any(
            entity.name in ("张明",) and entity.entity_type == "person"
            for entity in extracted.entities
        )
        record(
            "人名被归为 person（不是 skill）",
            person_ok,
            f"张明的种类="
            f"{[e.entity_type for e in extracted.entities if e.name == '张明']}",
        )

        # 技术名应被识别为 technology
        tech_ok = any(
            entity.name == "PostgreSQL"
            and entity.entity_type == "technology"
            for entity in extracted.entities
        )
        record(
            "技术名被归为 technology",
            tech_ok,
            f"PostgreSQL 的种类="
            f"{[e.entity_type for e in extracted.entities if e.name == 'PostgreSQL']}",
        )

        # 所有关系的两端都必须是抽出的实体
        names = {entity.name for entity in extracted.entities}
        record(
            "所有关系的两端都在实体列表里",
            all(
                relation.subject in names
                and relation.object in names
                for relation in extracted.relations
            ),
        )

    # ================================================
    print()
    print("=" * 74)
    print("3. 幂等落库")
    print("=" * 74)

    # 造一个临时项目，落库两次，验证不产生重复
    created_project_id = None
    chunk_id = None

    try:
        async with AsyncSessionLocal() as db:
            user_id = (
                await db.execute(
                    text(
                        "SELECT id FROM users WHERE username = :u"
                    ),
                    {"u": "mindflow"},
                )
            ).scalar_one()

            created_project_id = (
                await db.execute(
                    text(
                        "INSERT INTO projects "
                        "(name, description, owner_id, status, "
                        " created_at, updated_at) "
                        "VALUES ('图谱验证项目', NULL, :u, 'active', "
                        " now(), now()) RETURNING id"
                    ),
                    {"u": user_id},
                )
            ).scalar_one()

            # 需要一段真实 chunk 作为来源（外键约束）
            chunk_id = (
                await db.execute(
                    text(
                        "SELECT id FROM document_chunks LIMIT 1"
                    )
                )
            ).scalar_one_or_none()

            await db.commit()

        if chunk_id is None:
            record("库中没有 chunk，无法验证落库", False, "跳过")
        else:
            if extracted is None:
                record("抽取失败，跳过落库验证", False)
            else:
                async with AsyncSessionLocal() as db:
                    repository = KnowledgeRepository(db)

                    first = await repository.store_chunk_extraction(
                        project_id=created_project_id,
                        chunk_id=chunk_id,
                        result=extracted,
                    )

                    # 同样的输入**再存一次**
                    second = await repository.store_chunk_extraction(
                        project_id=created_project_id,
                        chunk_id=chunk_id,
                        result=extracted,
                    )

                    counts = await repository.count_for_project(
                        created_project_id
                    )

                record(
                    "首次落库写入了数据",
                    first[0] > 0,
                    f"实体 {first[0]}、关系 {first[1]}",
                )
                record(
                    "**重复落库不产生新数据（幂等）**",
                    second == (0, 0),
                    f"第二次写入 {second}",
                )
                record(
                    "库里的实体数与首次一致",
                    counts[0] == first[0],
                    f"库中 {counts[0]} 个实体",
                )

    finally:
        if created_project_id:
            async with AsyncSessionLocal() as db:
                # 关系与实体会随项目级联删除
                await db.execute(
                    text("DELETE FROM projects WHERE id = :p"),
                    {"p": created_project_id},
                )
                await db.commit()
            print(f"      已清理临时项目 {created_project_id}")

    print()
    print("=" * 74)
    passed = sum(1 for _, ok in results if ok)
    failed = [label for label, ok in results if not ok]
    print(f"通过 {passed} / {len(results)}")
    if failed:
        print("失败项:")
        for label in failed:
            print("  -", label)
        return 1

    print("全部通过")
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
