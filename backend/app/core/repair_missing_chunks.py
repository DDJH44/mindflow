"""回填"有内容但没有文本块"的文档。

背景：库里有 5 份文档停在"已解析"、`content` 有 163 字符、却
**一个 chunk 都没有**。它们来自 2026-09-14 —— 那时切块还没接入
上传流程（上传只解析、不切块）。

危害是静默的：这类文档**检索永远命中不到**，而界面上看不出异常
（状态是"已解析"，不算错误）。用户会以为自己传的资料在生效。

现在上传流程已经有守卫（有内容却切不出块会标 failed），
因此这个脚本只处理**历史数据**。

用法：

    # 只检查
    uv run python -m app.core.repair_missing_chunks

    # 检查并回填（切块 + 写入向量索引）
    uv run python -m app.core.repair_missing_chunks --repair
"""

import argparse
import asyncio
import sys

from sqlalchemy import text

from app.database.session import AsyncSessionLocal

# 与上传流程一致的切块参数。
# 不一致会让回填的文档与正常上传的文档在检索表现上不同，
# 而那种差异极难排查。
CHUNK_SIZE = 500
CHUNK_OVERLAP = 100


async def find_documents() -> list[tuple[int, str, int]]:
    """返回 [(document_id, name, content_len), ...]。"""

    async with AsyncSessionLocal() as db:
        rows = list(
            (
                await db.execute(
                    text(
                        """
                        SELECT d.id, d.name,
                               LENGTH(COALESCE(d.content, '')) AS len
                        FROM documents d
                        WHERE COALESCE(d.content, '') <> ''
                          AND NOT EXISTS (
                              SELECT 1 FROM document_chunks c
                              WHERE c.document_id = d.id
                          )
                        ORDER BY d.id
                        """
                    )
                )
            ).all()
        )

    return [
        (row[0], row[1], row[2])
        for row in rows
    ]


async def main() -> int:
    parser = argparse.ArgumentParser(
        description="回填缺少文本块的文档",
    )
    parser.add_argument(
        "--repair",
        action="store_true",
        help="执行回填（切块 + 写入向量索引）",
    )
    args = parser.parse_args()

    print("=" * 74)
    print("缺少文本块的文档检查")
    print("=" * 74)

    documents = await find_documents()

    print(f"  有内容但没有块的文档数: {len(documents)}")

    if not documents:
        print()
        print("  ✓ 没有需要回填的文档")
        return 0

    for document_id, name, length in documents:
        print(
            f"    doc {document_id} | {length} 字符 | {name}"
        )

    if not args.repair:
        print()
        print("  加 --repair 执行回填")
        return 1

    print()
    print("正在回填…")

    from app.services.chunking_service import ChunkingService
    from app.services.embedding_pipeline_service import (
        EmbeddingPipelineService,
    )
    from app.repositories.document_chunk_repository import (
        DocumentChunkRepository,
    )

    success = 0
    failed: list[tuple[int, str]] = []

    for document_id, name, _length in documents:
        try:
            async with AsyncSessionLocal() as db:
                content = (
                    await db.execute(
                        text(
                            "SELECT content, document_type "
                            "FROM documents WHERE id = :d"
                        ),
                        {"d": document_id},
                    )
                ).first()

            if not content or not (content[0] or "").strip():
                print(f"    doc {document_id}: 内容为空，跳过")
                continue

            chunks = ChunkingService.split_text(
                content[0],
                chunk_size=CHUNK_SIZE,
                chunk_overlap=CHUNK_OVERLAP,
            )

            if not chunks:
                print(f"    doc {document_id}: 切不出块，跳过")
                failed.append((document_id, "切不出块"))
                continue

            async with AsyncSessionLocal() as db:
                repository = DocumentChunkRepository(db)
                await repository.create_chunks(
                    document_id=document_id,
                    chunks=chunks,
                    chunk_metadata={
                        "document_type": content[1] or "other",
                    },
                )

                await db.execute(
                    text(
                        "UPDATE documents SET status = 'chunked' "
                        "WHERE id = :d"
                    ),
                    {"d": document_id},
                )
                await db.commit()

            # 写入向量索引
            async with AsyncSessionLocal() as db:
                embedded = await EmbeddingPipelineService(
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

            print(
                f"    doc {document_id}: 切 {len(chunks)} 块、"
                f"嵌入 {embedded} 段 → embedded"
            )
            success += 1

        except Exception as exc:  # noqa: BLE001
            print(
                f"    doc {document_id}: 失败 "
                f"{type(exc).__name__}: {str(exc)[:80]}"
            )
            failed.append((document_id, type(exc).__name__))

    print()
    print(f"  成功回填: {success}")
    if failed:
        print(f"  失败: {failed}")

    # 复检
    remaining = await find_documents()
    print(f"  复检剩余: {len(remaining)}")

    if remaining:
        print()
        print("  ✗ 仍有未回填的文档")
        return 1

    print()
    print("  ✓ 回填完成")
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
