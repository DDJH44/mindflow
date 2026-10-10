"""验证异步文档索引（§28）。

要防的缺陷：把"嵌入"放回请求路径（大文件阻塞 30 秒以上），
以及异步化带来的新故障模式：

| 故障 | 表现 |
| --- | --- |
| 任务不是原子领取 | 同一文档被嵌入两次 —— **白花钱且完全看不出来** |
| 没有租约恢复 | worker 崩溃后任务永远 `running`，界面一直转圈 |
| 没有重试上限 | 因内容问题的文档无限重试，持续消耗额度 |
| 入队不幂等 | 连点"重试索引"产生多个任务 |

用法：uv run python -m app.core.test_async_indexing
"""

import asyncio
import sys
import uuid

import httpx
from sqlalchemy import text as sql_text
from sqlalchemy.ext.asyncio import async_sessionmaker

from app.core.config import settings
from app.database.session import AsyncSessionLocal, engine
from app.services.document_index_worker import (
    MAX_ATTEMPTS,
    DocumentIndexWorker,
)

BASE = "http://127.0.0.1:5173/api"
ACCOUNT = "mindflow"

results: list[tuple[str, bool]] = []


def record(label: str, ok: bool, extra: str = "") -> None:
    results.append((label, ok))
    suffix = f"  {extra}" if extra else ""
    print(f"  [{'OK  ' if ok else 'FAIL'}] {label}{suffix}")


async def count_jobs(document_id: int) -> int:
    async with AsyncSessionLocal() as db:
        return (
            await db.execute(
                sql_text(
                    "SELECT COUNT(*) FROM document_index_jobs "
                    "WHERE document_id = :d"
                ),
                {"d": document_id},
            )
        ).scalar_one()


async def job_status(document_id: int) -> tuple | None:
    async with AsyncSessionLocal() as db:
        return (
            await db.execute(
                sql_text(
                    "SELECT status, attempts FROM document_index_jobs "
                    "WHERE document_id = :d"
                ),
                {"d": document_id},
            )
        ).first()


async def wait_for_status(
    client: httpx.AsyncClient,
    project_id: int,
    document_id: int,
    targets: tuple[str, ...],
    timeout: float = 180.0,
) -> str | None:
    """轮询文档状态直到进入目标集合。"""

    elapsed = 0.0

    while elapsed < timeout:
        documents = (
            await client.get(f"/projects/{project_id}/documents")
        ).json()
        found = [
            item for item in documents if item["id"] == document_id
        ]

        if found and found[0]["status"] in targets:
            return found[0]["status"]

        await asyncio.sleep(1)
        elapsed += 1

    return None


async def main() -> int:
    sys.path.insert(0, r"D:\yuxi\MindFlow\backend")

    from app.core.security import create_access_token

    async with AsyncSessionLocal() as db:
        user_id = (
            await db.execute(
                sql_text(
                    "SELECT id FROM users WHERE username = :u"
                ),
                {"u": ACCOUNT},
            )
        ).scalar_one()

    token = create_access_token({"sub": str(user_id)})
    created_documents: list[int] = []
    project_id: int | None = None

    # 供"离线小节"使用的假任务
    synthetic_docs: list[int] = []

    try:
        async with httpx.AsyncClient(
            base_url=BASE,
            headers={"Authorization": f"Bearer {token}"},
            timeout=600.0,
        ) as client:
            project_id = (
                await client.post(
                    "/projects",
                    json={"name": f"异步索引验证 {uuid.uuid4().hex[:6]}"},
                )
            ).json()["id"]

            # ================================================
            print()
            print("=" * 74)
            print("1. 上传立即返回（不再等索引）")
            print("=" * 74)

            body = ("这是一段用于验证异步索引的中文文本。" * 2000)[
                :100_000
            ].encode("utf-8")

            import time

            started = time.monotonic()
            response = await client.post(
                f"/projects/{project_id}/documents",
                params={"document_type": "resume"},
                files={"file": ("async.txt", body, "text/plain")},
            )
            upload_seconds = time.monotonic() - started

            record(
                "上传返回 202（已受理，索引进行中）",
                response.status_code == 202,
                f"status={response.status_code}",
            )

            document = (
                response.json()
                if response.status_code == 202
                else {}
            )
            document_id = document.get("id")

            if document_id:
                created_documents.append(document_id)

            record(
                "上传时状态为 chunked（已切块·待索引）",
                document.get("status") == "chunked",
                f"status={document.get('status')}",
            )

            # 这是本次改造的**核心收益**。阈值取 5 秒：
            # 100KB（约 120 块）同步嵌入需要 5 秒以上，
            # 而异步只做解析与切块。
            record(
                "上传耗时 < 5 秒（同步嵌入需要更久）",
                upload_seconds < 5,
                f"{upload_seconds:.2f}s",
            )

            # ================================================
            print()
            print("=" * 74)
            print("2. 入队幂等")
            print("=" * 74)

            if document_id:
                jobs = await count_jobs(document_id)
                record(
                    "上传后恰好有 1 个任务",
                    jobs == 1,
                    f"任务数={jobs}",
                )

                # 再入队两次（模拟连点重试）
                await client.post(
                    f"/projects/{project_id}/documents/"
                    f"{document_id}/embed"
                )
                await client.post(
                    f"/projects/{project_id}/documents/"
                    f"{document_id}/embed"
                )

                jobs = await count_jobs(document_id)
                record(
                    "重复入队不产生新任务",
                    jobs == 1,
                    f"任务数={jobs}",
                )

            # ================================================
            print()
            print("=" * 74)
            print("3. worker 把它索引完成")
            print("=" * 74)

            if document_id:
                final = await wait_for_status(
                    client,
                    project_id,
                    document_id,
                    ("embedded", "failed"),
                )

                record(
                    "worker 把状态推进到 embedded",
                    final == "embedded",
                    f"最终状态={final}",
                )

                status = await job_status(document_id)
                record(
                    "任务状态为 done",
                    status is not None and status[0] == "done",
                    f"任务={tuple(status) if status else None}",
                )

            # ================================================
            print()
            print("=" * 74)
            print("4. 原子领取（并发 worker 不重复）")
            print("=" * 74)

            # 造 3 个真实文档 + 3 个任务，然后用 6 个并发 worker 抢
            async with AsyncSessionLocal() as db:
                for index in range(3):
                    doc_id = (
                        await db.execute(
                            sql_text(
                                "INSERT INTO documents "
                                "(name, original_filename, file_type, "
                                " document_type, project_id, status, "
                                " created_at, updated_at) "
                                "VALUES (:n, :n, 'txt', 'resume', :p, "
                                " 'chunked', now(), now()) RETURNING id"
                            ),
                            {
                                "n": f"原子领取探针{index}",
                                "p": project_id,
                            },
                        )
                    ).scalar_one()
                    synthetic_docs.append(doc_id)

                    await db.execute(
                        sql_text(
                            "INSERT INTO document_index_jobs "
                            "(document_id, status) "
                            "VALUES (:d, 'pending')"
                        ),
                        {"d": doc_id},
                    )
                await db.commit()

            session_factory = async_sessionmaker(
                bind=engine, expire_on_commit=False
            )
            worker = DocumentIndexWorker(session_factory)

            claimed = await asyncio.gather(
                *[worker.claim_one() for _ in range(6)]
            )
            claimed = [item for item in claimed if item is not None]

            job_ids = [item[0] for item in claimed]

            record(
                "6 个并发 worker 只领到 3 个任务",
                len(claimed) == 3,
                f"领到 {len(claimed)} 个",
            )
            record(
                "**没有重复领取同一个任务**",
                len(job_ids) == len(set(job_ids)),
                f"任务 id={sorted(job_ids)}",
            )

            # 把它们的任务状态复位，避免后续小节干扰
            async with AsyncSessionLocal() as db:
                await db.execute(
                    sql_text(
                        "UPDATE document_index_jobs "
                        "SET status = 'pending', attempts = 0, "
                        "lease_expires_at = NULL "
                        "WHERE document_id = ANY(:d)"
                    ),
                    {"d": synthetic_docs},
                )
                await db.commit()

            # ================================================
            print()
            print("=" * 74)
            print("5. 租约恢复（worker 崩溃后的自愈）")
            print("=" * 74)

            # 模拟"任务被领走但 worker 崩了"：状态 running +
            # 租约已过期。没有恢复机制时它会永远卡在那里。
            async with AsyncSessionLocal() as db:
                await db.execute(
                    sql_text(
                        "UPDATE document_index_jobs "
                        "SET status = 'running', "
                        "    lease_expires_at = now() - interval '1 hour' "
                        "WHERE document_id = :d"
                    ),
                    {"d": synthetic_docs[0]},
                )
                await db.commit()

            recovered = await worker.recover_expired_leases()

            record(
                "过期租约被恢复为 pending",
                recovered >= 1,
                f"恢复了 {recovered} 个",
            )

            status = await job_status(synthetic_docs[0])
            record(
                "该任务状态回到 pending",
                status is not None and status[0] == "pending",
                f"任务={tuple(status) if status else None}",
            )

            # 未过期的 running 不该被抢走 —— 否则正在跑的任务
            # 会被第二个 worker 重复执行。
            async with AsyncSessionLocal() as db:
                await db.execute(
                    sql_text(
                        "UPDATE document_index_jobs "
                        "SET status = 'running', "
                        "    lease_expires_at = now() + interval '1 hour' "
                        "WHERE document_id = :d"
                    ),
                    {"d": synthetic_docs[1]},
                )
                await db.commit()

            await worker.recover_expired_leases()

            status = await job_status(synthetic_docs[1])
            record(
                "**未过期的租约不被抢走**",
                status is not None and status[0] == "running",
                f"任务={tuple(status) if status else None}",
            )

            # ================================================
            print()
            print("=" * 74)
            print("6. 重试上限")
            print("=" * 74)

            # 直接验证 finish() 的收尾逻辑：
            # attempts 未达上限 → 回 pending；达到 → failed。
            async with AsyncSessionLocal() as db:
                await db.execute(
                    sql_text(
                        "UPDATE document_index_jobs "
                        "SET status = 'running', attempts = 1 "
                        "WHERE document_id = :d"
                    ),
                    {"d": synthetic_docs[2]},
                )
                await db.commit()

            job = await job_status(synthetic_docs[2])
            job_id = None
            async with AsyncSessionLocal() as db:
                job_id = (
                    await db.execute(
                        sql_text(
                            "SELECT id FROM document_index_jobs "
                            "WHERE document_id = :d"
                        ),
                        {"d": synthetic_docs[2]},
                    )
                ).scalar_one()

            await worker.finish(job_id, error="模拟失败")

            status = await job_status(synthetic_docs[2])
            record(
                "未达上限时回到 pending（会重试）",
                status is not None and status[0] == "pending",
                f"任务={tuple(status) if status else None}",
            )

            # 把 attempts 推到上限再失败一次
            async with AsyncSessionLocal() as db:
                await db.execute(
                    sql_text(
                        "UPDATE document_index_jobs "
                        "SET status = 'running', attempts = :m "
                        "WHERE id = :i"
                    ),
                    {"m": MAX_ATTEMPTS, "i": job_id},
                )
                await db.commit()

            await worker.finish(job_id, error="模拟失败")

            status = await job_status(synthetic_docs[2])
            record(
                "达到上限后标 failed（停止重试）",
                status is not None and status[0] == "failed",
                f"任务={tuple(status) if status else None} "
                f"（上限 {MAX_ATTEMPTS}）",
            )

            # ================================================
            print()
            print("=" * 74)
            print("7. 没有 chunk 的文档不该被排队")
            print("=" * 74)

            async with AsyncSessionLocal() as db:
                empty_doc = (
                    await db.execute(
                        sql_text(
                            "INSERT INTO documents "
                            "(name, original_filename, file_type, "
                            " document_type, project_id, status, "
                            " created_at, updated_at) "
                            "VALUES (:n, :n, 'txt', 'resume', :p, "
                            " 'parsed', now(), now()) RETURNING id"
                        ),
                        {"n": "无块文档探针", "p": project_id},
                    )
                ).scalar_one()
                await db.commit()

            synthetic_docs.append(empty_doc)

            response = await client.post(
                f"/projects/{project_id}/documents/{empty_doc}/embed"
            )

            record(
                "无文本块的文档返回 400（并说明原因）",
                response.status_code == 400,
                f"status={response.status_code} "
                f"{response.json().get('detail', '')[:40]}",
            )

            jobs = await count_jobs(empty_doc)
            record(
                "**没有为它创建任务**（避免 worker 空转）",
                jobs == 0,
                f"任务数={jobs}",
            )

    finally:
        # 清理：先删任务与文档，再删项目
        async with AsyncSessionLocal() as db:
            all_docs = synthetic_docs + created_documents

            if all_docs:
                await db.execute(
                    sql_text(
                        "DELETE FROM document_index_jobs "
                        "WHERE document_id = ANY(:d)"
                    ),
                    {"d": all_docs},
                )
                await db.execute(
                    sql_text(
                        "DELETE FROM document_chunks "
                        "WHERE document_id = ANY(:d)"
                    ),
                    {"d": all_docs},
                )
                await db.execute(
                    sql_text(
                        "DELETE FROM documents WHERE id = ANY(:d)"
                    ),
                    {"d": all_docs},
                )

            if project_id:
                await db.execute(
                    sql_text("DELETE FROM projects WHERE id = :p"),
                    {"p": project_id},
                )

            await db.commit()

        print(f"      已清理项目 {project_id} 与文档 {len(synthetic_docs) + len(created_documents)} 份")

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
