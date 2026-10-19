"""重新嵌入**全部**已索引文档（MIND_FLOW_PLAN.md §29 流程的第 4 步）。

## 什么时候必须跑

**换 embedding 模型之后。** 不同模型的向量空间不兼容 ——
即使维度相同（v3 与 v4 都是 1024），用新模型编码 query
去和旧模型编码的 chunk 比余弦距离，得到的相似度**没有意义**
（不是变差，是无意义）。

而且这种失效是**静默的**：检索照常返回结果、不报错，
只是那些结果与问题无关。因此换模型后**必须**重嵌，
否则"已索引 · 可检索"这个界面状态是骗人的。

## 为什么是原地覆写而不是清空重建

`MilvusVectorStore.insert` 用 **chunk id 作为主键**，
因此重嵌会覆写该 id 的向量，**不会堆积旧向量**。
这比"删集合重建"安全：中途失败时，未重嵌的 chunk
仍保留旧向量（结果不准，但不会消失）。

## 用法

    # 先看要处理什么（不改任何数据）
    uv run python -m scripts.reembed_all --dry-run

    # 真正执行
    uv run python -m scripts.reembed_all

    # 顺便清掉没有对应 chunk 的孤儿向量
    uv run python -m scripts.reembed_all --purge-orphans
"""

import argparse
import asyncio
import sys

from sqlalchemy import text

from app.database.session import AsyncSessionLocal


async def main() -> int:
    parser = argparse.ArgumentParser(
        description="重新嵌入全部已索引文档",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="只列出要做什么，不改数据",
    )
    parser.add_argument(
        "--purge-orphans",
        action="store_true",
        help="结束后清理孤儿向量",
    )
    args = parser.parse_args()

    from app.core.config import settings
    from app.services.embedding_pipeline_service import (
        EmbeddingPipelineService,
    )

    print("=" * 74)
    print("重新嵌入全部文档")
    print("=" * 74)
    print(f"  当前模型: {settings.embedding_model}")
    print(f"  维度    : 由端点决定（换模型时必须与集合一致）")
    print()

    async with AsyncSessionLocal() as db:
        documents = list(
            (
                await db.execute(
                    text(
                        "SELECT d.id, d.name, d.project_id, "
                        "(SELECT COUNT(*) FROM document_chunks c "
                        " WHERE c.document_id = d.id) AS chunks "
                        "FROM documents d "
                        "WHERE d.status = 'embedded' "
                        "ORDER BY d.id"
                    )
                )
            ).all()
        )

        total_chunks = sum(row[3] for row in documents)

    if not documents:
        print("  没有状态为 embedded 的文档，无需处理。")
        return 0

    print(f"  待重嵌文档 {len(documents)} 份，共 {total_chunks} 段：")
    for document_id, name, project_id, chunks in documents:
        print(
            f"    doc {document_id:<4} 项目{project_id:<4} "
            f"{chunks:>3} 段  {str(name)[:40]}"
        )

    if args.dry_run:
        print()
        print("  （--dry-run：未修改任何数据）")
        return 0

    print()
    print("=" * 74)
    print("执行")
    print("=" * 74)

    # ---------------- 重置状态 ----------------
    #
    # 必须先把 chunk 标回 pending：`embed_document` 只处理
    # `get_pending_chunks` 返回的块，否则它会认为"无事可做"，
    # 于是脚本会报成功而实际什么都没重嵌。
    async with AsyncSessionLocal() as db:
        result = await db.execute(
            text(
                "UPDATE document_chunks SET embedding_status = 'pending' "
                "WHERE document_id = ANY(:ids)"
            ),
            {"ids": [row[0] for row in documents]},
        )
        await db.execute(
            text(
                "UPDATE documents SET status = 'pending' "
                "WHERE id = ANY(:ids)"
            ),
            {"ids": [row[0] for row in documents]},
        )
        await db.commit()

        print(f"  已重置 {result.rowcount} 段的嵌入状态为 pending")

    # ---------------- 重新嵌入 ----------------
    embedded_total = 0
    failed: list[tuple[int, str]] = []

    for document_id, name, _project_id, _chunks in documents:
        async with AsyncSessionLocal() as db:
            try:
                count = await EmbeddingPipelineService(
                    db
                ).embed_document(document_id)

                await db.execute(
                    text(
                        "UPDATE documents SET status = 'embedded' "
                        "WHERE id = :d"
                    ),
                    {"d": document_id},
                )
                await db.commit()

                embedded_total += count
                print(f"  ✓ doc {document_id:<4} 重嵌 {count} 段")

            except Exception as exc:  # noqa: BLE001
                await db.rollback()

                reason = f"{type(exc).__name__}: {str(exc)[:90]}"
                failed.append((document_id, reason))
                print(f"  ✗ doc {document_id:<4} 失败：{reason}")

    print()
    print("=" * 74)
    print(f"  重新嵌入 {embedded_total} 段，失败 {len(failed)} 份文档")

    if args.purge_orphans:
        print()
        print("清理孤儿向量…")
        from scripts.check_orphan_vectors import main as purge

        # 复用既有脚本：它自己会打印并返回退出码
        code = await purge(["--purge"])
        print(f"  check_orphan_vectors 退出码 {code}")

    print()
    print("  下一步必须验证一致性：")
    print("    uv run python -m scripts.check_orphan_vectors")
    print("    uv run python -m scripts.check_embedded_consistency")
    print("    uv run python -m app.evaluation.runner    # 重跑基线")

    if failed:
        print()
        print("  ⚠️ 有文档未能重嵌 —— 它们的向量仍是**旧模型**产生的，")
        print("     检索会返回无意义的结果，而界面上仍显示「已索引」。")
        return 1

    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
