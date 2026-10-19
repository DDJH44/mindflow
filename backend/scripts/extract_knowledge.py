"""知识图谱第 1 步：把材料抽成实体与关系（§30.3）。

## 为什么是按需工具，而不是接进上传流程

`§30.3` 把第 2 步（**关系扩展检索**）标为"真正见效的地方"，
而它还没实现。也就是说**现在抽出来的图谱没有任何消费者** ——
接进上传流程只会让索引变慢、多花钱，换不来任何可感知的收益。

因此这一步做成按需工具：要验证抽取质量、或第 2 步开工时再跑。

## 用法

    # 看某个项目的抽取结果（不落库）
    uv run python -m scripts.extract_knowledge --project 29 --dry-run

    # 真正落库
    uv run python -m scripts.extract_knowledge --project 29

    # 只处理某一份文档
    uv run scripts.extract_knowledge --document 54

## 它不做什么

不做关系扩展检索、不改检索逻辑（那是第 2 步）。
本工具只负责"把材料变成图"，并如实报告抽取质量 ——
§30.5 把"抽样人工核对准确率"列为验收项之一，
因此输出里会把实体与关系**全部列出来**，而不是只给个数。
"""

import argparse
import asyncio
import sys

from sqlalchemy import text

from app.database.session import AsyncSessionLocal


async def load_chunks(
    project_id: int | None,
    document_id: int | None,
    limit: int | None,
) -> list[tuple[int, int, str]]:
    """取 [(chunk_id, document_id, content)]。"""

    sql = (
        "SELECT c.id, c.document_id, c.content "
        "FROM document_chunks c "
        "JOIN documents d ON d.id = c.document_id "
        "WHERE d.status = 'embedded'"
    )

    params: dict = {}

    if project_id is not None:
        sql += " AND d.project_id = :p"
        params["p"] = project_id

    if document_id is not None:
        sql += " AND c.document_id = :d"
        params["d"] = document_id

    sql += " ORDER BY c.id"

    if limit is not None:
        sql += " LIMIT :n"
        params["n"] = limit

    async with AsyncSessionLocal() as db:
        rows = list((await db.execute(text(sql), params)).all())

    return [(row[0], row[1], row[2] or "") for row in rows]


async def main() -> int:
    parser = argparse.ArgumentParser(
        description="把材料抽成知识图谱的实体与关系",
    )
    parser.add_argument("--project", type=int, default=None)
    parser.add_argument("--document", type=int, default=None)
    parser.add_argument(
        "--limit",
        type=int,
        default=None,
        help="最多处理多少段（控制成本）",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="只抽取并打印，不落库",
    )
    args = parser.parse_args()

    if args.project is None and args.document is None:
        print("  需要 --project 或 --document 指定范围")
        print("  （不设范围会对全库抽取，那是非预期的开销）")
        return 2

    from app.repositories.knowledge_repository import (
        KnowledgeRepository,
    )
    from app.services.knowledge.extraction_service import (
        KnowledgeExtractionService,
    )

    chunks = await load_chunks(
        args.project, args.document, args.limit
    )

    print("=" * 74)
    print("知识图谱实体抽取")
    print("=" * 74)
    print(f"  待处理片段: {len(chunks)}")

    if not chunks:
        print()
        print("  没有可处理的片段（需要文档状态为 embedded）")
        return 1

    service = KnowledgeExtractionService()

    total_entities = 0
    total_relations = 0
    failed = 0

    for chunk_id, document_id, content in chunks:
        print()
        print(f"--- chunk {chunk_id}（文档 {document_id}，"
              f"{len(content)} 字符）---")

        try:
            result = await service.extract(content)
        except Exception as exc:  # noqa: BLE001
            failed += 1
            print(f"  ✗ 抽取失败：{type(exc).__name__}: {str(exc)[:100]}")
            continue

        print(f"  实体 {len(result.entities)} 个：")
        for entity in result.entities:
            print(f"    [{entity.entity_type:<12}] {entity.name}")

        if result.relations:
            print(f"  关系 {len(result.relations)} 条：")
            for relation in result.relations:
                print(
                    f"    {relation.subject} "
                    f"--{relation.predicate}--> {relation.object}"
                )
        else:
            print("  关系 0 条")

        total_entities += len(result.entities)
        total_relations += len(result.relations)

        if not args.dry_run:
            async with AsyncSessionLocal() as db:
                # 需要文档所属的项目
                project_id = (
                    await db.execute(
                        text(
                            "SELECT project_id FROM documents "
                            "WHERE id = :d"
                        ),
                        {"d": document_id},
                    )
                ).scalar_one()

                created = await KnowledgeRepository(
                    db
                ).store_chunk_extraction(
                    project_id=project_id,
                    chunk_id=chunk_id,
                    result=result,
                )

            print(f"  已落库：新增实体 {created[0]}、关系 {created[1]}")

    print()
    print("=" * 74)
    print(f"  片段 {len(chunks)} 个（失败 {failed}）")
    print(f"  抽出实体 {total_entities} 个、关系 {total_relations} 条")
    if args.dry_run:
        print("  （--dry-run：未落库）")

    if args.project is not None and not args.dry_run:
        async with AsyncSessionLocal() as db:
            counts = await KnowledgeRepository(
                db
            ).count_for_project(args.project)
        print(f"  项目 {args.project} 库中共计："
              f"实体 {counts[0]}、关系 {counts[1]}")

    if failed:
        print()
        print("  ⚠️ 有片段抽取失败 —— 它们在图谱里缺失，")
        print("     而缺失不会报错，只会让关联少一部分。重跑本工具即可补上。")
        return 1

    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
