"""资料上传与向量索引验证。

覆盖一个**静默断点**：文档上传后必须写入向量索引才能被检索到。
此前嵌入只存在于开发脚本里，上传接口从不触发它，
于是文档永远停在 `chunked` —— 检索为空、面试只出通用题，
而用户完全看不出哪里不对。

依赖：PostgreSQL、Milvus、嵌入服务（DashScope）。
运行：uv run python -m app.core.test_document_upload
"""

import asyncio
import sys

import httpx
from sqlalchemy import text

from app.core.security import create_access_token
from app.core.test_support import grant_quota, reset_quota
from app.database.session import AsyncSessionLocal
from app.main import app

USER_ID = 3
PROJECT_ID = 3

# 一段足以被切成多个 500 字符块的文本
SAMPLE_TEXT = (
    "项目经历：MindFlow AI 面试助手\n\n"
    "我负责后端开发，使用 FastAPI 构建接口层，"
    "PostgreSQL 存储结构化数据，Milvus 存放向量，Redis 做缓存。\n\n"
    "文档处理链路：用户上传简历后先解析为纯文本，"
    "按 500 字符切块、overlap 100，再用 text-embedding-v4 "
    "转成 1024 维向量写入 Milvus。\n\n"
    "检索时对查询做同样的向量化，带 project_id 过滤取 top-k，"
    "按 chunk_id 回 PostgreSQL 取回正文，最后交给大模型生成面试问题。\n\n"
    "遇到的坑：最初切块只用固定长度，跨界处的项目经历被截断，"
    "导致检索只命中半个项目上下文，后来加入重叠窗口缓解。\n"
) * 3

results: list[tuple[str, bool]] = []


def record(label: str, ok: bool, extra: str = "") -> None:
    results.append((label, ok))
    suffix = f"  {extra}" if extra else ""
    print(f"  [{'OK  ' if ok else 'FAIL'}] {label}{suffix}")


async def cleanup(client, document_ids: list[int]) -> None:
    """通过 DELETE 端点清理测试文档。

    刻意**不**直接删库：这样清理本身就是对删除端点的一次验证，
    也能确认向量索引一起被清掉（孤儿向量会占用 top-k 名额）。
    """

    for document_id in document_ids:
        try:
            response = await client.delete(
                f"/api/projects/{PROJECT_ID}/documents/{document_id}"
            )
            print(
                f"      清理文档 {document_id}: HTTP "
                f"{response.status_code}"
            )
        except Exception as exc:
            print(f"      清理文档 {document_id} 失败: {exc}")

    # 兜底：若端点删除失败，直接删库避免留下测试数据。
    #
    # ⚠️ **必须同时删向量**：直接删库不会经过删除端点，
    # 因此 chunk 与 Milvus 里的向量都会留下 ——
    # 那些孤儿向量会占用 top-k 名额、静默降低召回（D52）。
    # 此前这里只删了库，留下的向量要等到某次
    # `check_orphan_vectors --purge` 才被发现。
    from app.services.milvus_vector_store import MilvusVectorStore

    async with AsyncSessionLocal() as db:
        for document_id in document_ids:
            chunk_ids = [
                row[0]
                for row in (
                    await db.execute(
                        text(
                            "SELECT id FROM document_chunks "
                            "WHERE document_id = :d"
                        ),
                        {"d": document_id},
                    )
                ).all()
            ]

            if chunk_ids:
                try:
                    await MilvusVectorStore().delete(ids=chunk_ids)
                except Exception as exc:  # noqa: BLE001
                    print(
                        f"      清理向量失败 doc {document_id}: {exc}"
                    )

            await db.execute(
                text("DELETE FROM documents WHERE id = :d"),
                {"d": document_id},
            )
        await db.commit()


async def main():
    await grant_quota(USER_ID)

    token = create_access_token({"sub": str(USER_ID)})
    headers = {"Authorization": f"Bearer {token}"}
    transport = httpx.ASGITransport(app=app)

    created_documents: list[int] = []
    client = httpx.AsyncClient(
        transport=transport,
        base_url="http://test",
        headers=headers,
        timeout=600.0,
    )

    # 清理必须在客户端**仍打开**时执行 —— 用 DELETE 端点而不是直接删库，
    # 这样清理本身就是对删除端点的一次验证。
    #
    # 曾把清理放在 `async with client:` 之外，结果客户端已关闭、
    # 清理静默失败（打印 "client has been closed"），
    # 靠兜底的直接删库才没留下垃圾数据。
    try:
        async with client:

            # ================================================
            # 1. 列表端点
            # ================================================
            print()
            print("=" * 74)
            print("1. 列出项目资料")
            print("=" * 74)

            response = await client.get(
                f"/api/projects/{PROJECT_ID}/documents"
            )
            record(
                "列表返回 200",
                response.status_code == 200,
                f"status={response.status_code}",
            )

            before_count = len(response.json())
            print(f"      上传前已有 {before_count} 份资料")

            response = await client.get(
                "/api/projects/999999/documents"
            )
            record(
                "不存在的项目返回 404",
                response.status_code == 404,
                f"status={response.status_code}",
            )

            # ================================================
            # 2. 上传并自动建立索引
            # ================================================
            print()
            print("=" * 74)
            print("2. 上传资料（含解析 + 切块 + 建立索引）")
            print("=" * 74)

            response = await client.post(
                f"/api/projects/{PROJECT_ID}/documents",
                params={"document_type": "resume"},
                files={
                    "file": (
                        "resume_upload_test.txt",
                        SAMPLE_TEXT.encode("utf-8"),
                        "text/plain",
                    )
                },
            )
            record(
                "上传返回 201",
                response.status_code == 201,
                f"status={response.status_code}",
            )

            if response.status_code != 201:
                print("      响应体:", response.text[:300])

            body = response.json() if response.status_code == 201 else {}
            document_id = body.get("id")
            if document_id:
                created_documents.append(document_id)

            record(
                "记录了资料类型",
                body.get("document_type") == "resume",
                f"type={body.get('document_type')}",
            )

            # 这是本次修复的核心断言
            record(
                "状态为 embedded（已写入向量索引）",
                body.get("status") == "embedded",
                f"status={body.get('status')}",
            )

            # ================================================
            # 3. 切块与索引落库
            # ================================================
            print()
            print("=" * 74)
            print("3. 切块与索引是否真的落库")
            print("=" * 74)

            if document_id:
                async with AsyncSessionLocal() as db:
                    total = (
                        await db.execute(
                            text(
                                "SELECT COUNT(*) FROM document_chunks "
                                "WHERE document_id = :d"
                            ),
                            {"d": document_id},
                        )
                    ).scalar_one()
                    embedded = (
                        await db.execute(
                            text(
                                "SELECT COUNT(*) FROM document_chunks "
                                "WHERE document_id = :d "
                                "AND embedding_status = 'embedded'"
                            ),
                            {"d": document_id},
                        )
                    ).scalar_one()

                record(
                    "文本已切块",
                    total > 0,
                    f"共 {total} 段",
                )
                record(
                    "全部文本块已标记为 embedded",
                    total > 0 and embedded == total,
                    f"{embedded} / {total}",
                )

            # ================================================
            # 4. 幂等性：重复建立索引
            # ================================================
            print()
            print("=" * 74)
            print("4. 重复建立索引（幂等）")
            print("=" * 74)

            if document_id:
                response = await client.post(
                    f"/api/projects/{PROJECT_ID}/documents/"
                    f"{document_id}/embed"
                )
                record(
                    "重试索引返回 200",
                    response.status_code == 200,
                    f"status={response.status_code}",
                )
                record(
                    "重复调用后状态仍为 embedded",
                    response.json().get("status") == "embedded",
                    f"status={response.json().get('status')}",
                )

            # ================================================
            # 5. 所有权校验
            # ================================================
            print()
            print("=" * 74)
            print("5. 所有权校验")
            print("=" * 74)

            async with AsyncSessionLocal() as db:
                other_project = (
                    await db.execute(
                        text(
                            "SELECT id FROM projects "
                            "WHERE owner_id <> :uid LIMIT 1"
                        ),
                        {"uid": USER_ID},
                    )
                ).scalar_one_or_none()

            if other_project:
                response = await client.get(
                    f"/api/projects/{other_project}/documents"
                )
                record(
                    "他人项目返回 403",
                    response.status_code == 403,
                    f"status={response.status_code}",
                )
            else:
                print("      （库中没有他人项目，跳过该用例）")

            if document_id:
                response = await client.post(
                    f"/api/projects/{PROJECT_ID}/documents/"
                    f"999999/embed"
                )
                record(
                    "对不存在的文档建立索引返回 404",
                    response.status_code == 404,
                    f"status={response.status_code}",
                )

            # ================================================
            # 6. 上传后能被检索到（端到端效果）
            # ================================================
            print()
            print("=" * 74)
            print("6. 上传的资料能否被检索到（这是真正要验证的效果）")
            print("=" * 74)

            from app.services.retrieval_service import RetrievalService

            async with AsyncSessionLocal() as db:
                service = RetrievalService(db)
                chunks = await service.retrieve(
                    query="chunk 切分策略与重叠窗口",
                    project_id=PROJECT_ID,
                    limit=5,
                )

            # retrieve 返回的是 list[dict]（键含 document_id / chunk_id），
            # 不是 ORM 对象 —— 用属性访问会静默拿到 None。
            record(
                "检索能命中刚上传的资料",
                any(
                    chunk.get("document_id") == document_id
                    for chunk in chunks
                ),
                f"命中 {len(chunks)} 段",
            )

            # ================================================
            # 7. 列表能看到新资料
            # ================================================
            print()
            print("=" * 74)
            print("7. 列表包含新上传的资料")
            print("=" * 74)

            response = await client.get(
                f"/api/projects/{PROJECT_ID}/documents"
            )
            items = response.json()
            record(
                "列表数量 +1",
                len(items) == before_count + 1,
                f"{before_count} → {len(items)}",
            )
            record(
                "列表中的新资料状态为 embedded",
                any(
                    item["id"] == document_id
                    and item["status"] == "embedded"
                    for item in items
                ),
            )

            # ================================================
            # 8. 删除（含向量）—— 必须在客户端关闭前调用
            # ================================================
            print()
            print("=" * 74)
            print("8. 删除文档（含向量索引）")
            print("=" * 74)

            if document_id:
                response = await client.delete(
                    f"/api/projects/{PROJECT_ID}/documents/"
                    f"{document_id}"
                )
                record(
                    "删除返回 204",
                    response.status_code == 204,
                    f"status={response.status_code}",
                )

                async with AsyncSessionLocal() as db:
                    left = (
                        await db.execute(
                            text(
                                "SELECT COUNT(*) FROM document_chunks "
                                "WHERE document_id = :d"
                            ),
                            {"d": document_id},
                        )
                    ).scalar_one()

                # 删除后它的 chunk 必须一起消失，否则会留下
                # 占用 top-k 名额却取不到正文的孤儿向量（D52）
                record(
                    "删除后文本块一并清除",
                    left == 0,
                    f"剩余 {left} 段",
                )

                response = await client.get(
                    f"/api/projects/{PROJECT_ID}/documents"
                )
                record(
                    "列表不再包含已删除的资料",
                    all(
                        item["id"] != document_id
                        for item in response.json()
                    ),
                )

                # 已通过端点删除，清理时不必再删
                created_documents.clear()

            # ================================================
            # 9. 大文件上传（嵌入分批）
            # ================================================
            print()
            print("=" * 74)
            print("9. 大文件上传（验证嵌入分批）")
            print("=" * 74)

            # 端点的嵌入批量上限约为 10 条。
            # 此前 `embed_texts` 把整个文档的 chunk 一次性发出，
            # 因此**超过约 1 万字符（25 块）的文件必然上传失败**，
            # 而报错是 502「服务不可用」——
            # 用户完全看不出问题出在文件长度上。
            #
            # 端点的嵌入批量上限约为 10 条。
            # 此前 `embed_texts` 把整个文档的 chunk 一次性发出，
            # 因此**超过约 1 万字符（25 块）的文件必然上传失败**，
            # 而报错是 502「服务不可用」——
            # 用户完全看不出问题出在文件长度上。
            #
            # 200KB 约产出 450 块，远超上限。
            #
            # 文本里埋一个**唯一标识串**，后面用它检索回来 ——
            # 这样验证的是"向量真的可被检索"（真实用途），
            # 而不只是"代码走到了 embedded 这一步"。
            unique_marker = "ZYXVUTSRQPON"

            big_text = (
                "这是一段用于验证嵌入分批的中文文本，"
                "包含项目经历、技术选型与实现细节的描述。"
            ) * 1400 + f"\n\n关键标识：{unique_marker}。"

            response = await client.post(
                f"/api/projects/{PROJECT_ID}/documents",
                params={"document_type": "resume"},
                files={
                    "file": (
                        "big_upload_test.txt",
                        big_text.encode("utf-8"),
                        "text/plain",
                    )
                },
            )

            record(
                "大文件（约 200KB）上传返回 201",
                response.status_code == 201,
                f"status={response.status_code}",
            )

            if response.status_code != 201:
                print("      响应体:", response.text[:240])

            big_body = (
                response.json()
                if response.status_code == 201
                else {}
            )
            big_document_id = big_body.get("id")

            if big_document_id:
                created_documents.append(big_document_id)

            record(
                "大文件状态为 embedded",
                big_body.get("status") == "embedded",
                f"status={big_body.get('status')}",
            )

            if big_document_id:
                # 分批写入了多少块？必须远超单个批次的上限，
                # 否则这个用例没有覆盖到"需要分批"的情形。
                async with AsyncSessionLocal() as db:
                    chunk_count = (
                        await db.execute(
                            text(
                                "SELECT COUNT(*) FROM document_chunks "
                                "WHERE document_id = :d"
                            ),
                            {"d": big_document_id},
                        )
                    ).scalar_one()

                record(
                    "切块数远超单批上限（确实需要分批）",
                    chunk_count > 20,
                    f"共 {chunk_count} 块",
                )

                # 关键：向量必须真的进了 Milvus，而且**可被检索到**。
                #
                # 状态是 embedded 只说明代码走到了那一步；
                # 这里用埋在文本里的唯一标识串检索回来，
                # 验证的是真实用途。
                from app.services.retrieval_service import (
                    RetrievalService,
                )

                async with AsyncSessionLocal() as db:
                    hits = await RetrievalService(db).retrieve(
                        query=unique_marker,
                        project_id=PROJECT_ID,
                        limit=10,
                    )

                matched = [
                    hit
                    for hit in hits
                    if hit["document_id"] == big_document_id
                ]

                record(
                    "大文件的向量**可被检索到**（不只是状态对）",
                    bool(matched),
                    f"命中 {len(hits)} 段，其中属于本文档 "
                    f"{len(matched)} 段",
                )

                # ============================================
                # 10. 清理大文件并断言"真的清理干净"
                # ============================================
                #
                # **放在客户端仍打开时**：此前清理在 `finally` 里，
                # 而 `finally` 在 `async with client` 之外 ——
                # 那时客户端已关闭，HTTP 删除必然失败，
                # 只能退化成直接删库，于是**向量被留在 Milvus**
                # （实测 137 块 200KB 文档留下了 137 个孤儿向量）。
                #
                # 而且清理放在 `finally` 里意味着它发生在**所有断言
                # 之后** —— 清理本身从不被验证。这里改为显式清理
                # 并断言结果。
                print()
                print("=" * 74)
                print("10. 清理大文件并验证无残留")
                print("=" * 74)

                response = await client.delete(
                    f"/api/projects/{PROJECT_ID}/documents/"
                    f"{big_document_id}"
                )

                record(
                    "大文件删除返回 204",
                    response.status_code == 204,
                    f"status={response.status_code}",
                )

                async with AsyncSessionLocal() as db:
                    left = (
                        await db.execute(
                            text(
                                "SELECT COUNT(*) FROM document_chunks "
                                "WHERE document_id = :d"
                            ),
                            {"d": big_document_id},
                        )
                    ).scalar_one()

                record(
                    "删除后文本块一并清除",
                    left == 0,
                    f"剩余 {left} 段",
                )

                # 向量也必须没了 —— 这正是此前被漏掉的一环。
                # 用**检索**验证：删掉后不该再命中本文档。
                async with AsyncSessionLocal() as db:
                    hits_after = await RetrievalService(db).retrieve(
                        query=unique_marker,
                        project_id=PROJECT_ID,
                        limit=20,
                    )

                still_there = [
                    hit
                    for hit in hits_after
                    if hit["document_id"] == big_document_id
                ]

                record(
                    "删除后向量不再被检索到（无孤儿向量）",
                    not still_there,
                    f"仍命中 {len(still_there)} 段",
                )

                # 已通过端点删除，清理阶段不必再删
                created_documents.remove(big_document_id)

    finally:
        # 兜底：正常路径已在上面通过 DELETE 端点逐条删掉
        # （此时 `created_documents` 为空，这里是 no-op）；
        # 若中途失败，这里保证不留下测试数据。
        #
        # `cleanup` 自身也删向量（见其说明）—— 因为它可能在
        # 客户端已关闭时运行。
        await cleanup(client, created_documents)
        await reset_quota(USER_ID)

    print()
    print("=" * 74)
    passed = sum(1 for _, ok in results if ok)
    failed = [label for label, ok in results if not ok]

    print(f"通过 {passed} / {len(results)}")
    if failed:
        print("失败项:")
        for label in failed:
            print("  -", label)
        sys.exit(1)

    print("全部通过")


asyncio.run(main())
