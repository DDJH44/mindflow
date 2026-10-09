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

    # 兜底：若端点删除失败，直接删库避免留下测试数据
    async with AsyncSessionLocal() as db:
        for document_id in document_ids:
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

    finally:
        # 兜底清理：正常路径已在上面通过 DELETE 端点删掉
        # （此时 `created_documents` 已清空、这里是 no-op）；
        # 若中途失败，这里保证不留下测试数据。
        #
        # ⚠️ 注意：`finally` 在 `async with client` **之外**，
        # 此刻客户端已关闭，因此 cleanup 里的 HTTP 调用会失败。
        # 正常路径不受影响（列表为空），但**失败路径下删不掉** ——
        # 所以 cleanup 里保留了直接删库的兜底。
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
