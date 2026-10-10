"""验证"有内容却切不出块"的守卫（临时后端，需人手动启动）。

守卫在 `upload_document` 里：解析出内容但 `split_text` 返回空时，
文档标 `failed` 并返回 400，**不是静默留下一个检索不到的记录**。

正常情况下这条分支走不到（解析器已拒绝纯空白、`split_text` 对
非空内容至少产出 1 块），因此要临时把 `split_text` 打成永远返回空。

启动方式（另开一个 8098 端口）：

    uv run python -m app.core.upload_with_broken_chunker

然后跑本脚本：

    uv run python -m app.core.test_chunk_guard
"""

import asyncio
import sys
import uuid

import httpx
from sqlalchemy import text

from app.core.security import create_access_token
from app.database.session import AsyncSessionLocal

BASE = "http://127.0.0.1:8098/api"
ACCOUNT = "mindflow"

results: list[tuple[str, bool]] = []


def record(label: str, ok: bool, extra: str = "") -> None:
    results.append((label, ok))
    suffix = f"  {extra}" if extra else ""
    print(f"  [{'OK  ' if ok else 'FAIL'}] {label}{suffix}")


async def main() -> int:
    sys.path.insert(0, r"D:\yuxi\MindFlow\backend")

    async with AsyncSessionLocal() as db:
        user_id = (
            await db.execute(
                text("SELECT id FROM users WHERE username = :u"),
                {"u": ACCOUNT},
            )
        ).scalar_one()
        project_id = (
            await db.execute(
                text(
                    "SELECT id FROM projects "
                    "WHERE owner_id = :u ORDER BY id LIMIT 1"
                ),
                {"u": user_id},
            )
        ).scalar_one()

    token = create_access_token({"sub": str(user_id)})
    created_project = None

    async with httpx.AsyncClient(
        base_url=BASE,
        headers={"Authorization": f"Bearer {token}"},
        timeout=300.0,
    ) as client:
        response = await client.post(
            "/projects",
            json={"name": f"切块守卫验证 {uuid.uuid4().hex[:6]}"},
        )
        if response.status_code != 201:
            print(
                f"      建项目失败 {response.status_code}；"
                "请确认 8098 后端已启动"
            )
            return 1

        created_project = response.json()["id"]

        # 内容完全正常，但后端的切块器被打成永远返回空
        response = await client.post(
            f"/projects/{created_project}/documents",
            params={"document_type": "resume"},
            files={
                "file": (
                    "normal.txt",
                    (
                        "这是一份完全正常的文本内容，"
                        "正常情况下必然能切出至少一个块。"
                    ).encode("utf-8"),
                    "text/plain",
                )
            },
        )

        detail = ""
        try:
            detail = str(response.json().get("detail", ""))
        except Exception:  # noqa: BLE001
            detail = response.text[:200]

        print(f"      status={response.status_code}")
        print(f"      detail={detail[:140]}")

        record(
            "切不出块时返回 400（而不是 201 并静默留下死档）",
            response.status_code == 400,
            f"status={response.status_code}",
        )
        record(
            "提示说明了字符数",
            "字符" in detail,
        )

        # 关键：**不能留下"有内容、状态却不是 failed"的记录**。
        #
        # 为什么允许留下 `failed`：记录与文件都已落盘，标 failed
        # 让用户能在资料页看到并删除，这比静默消失好。
        # 真正要防的是**静默死档** —— 状态看起来正常
        # （parsed / chunked / embedded）却检索不到。
        async with AsyncSessionLocal() as db:
            suspicious = list(
                (
                    await db.execute(
                        text(
                            """
                            SELECT d.id, d.status
                            FROM documents d
                            WHERE d.project_id = :p
                              AND COALESCE(d.content, '') <> ''
                              AND d.status <> 'failed'
                              AND NOT EXISTS (
                                  SELECT 1 FROM document_chunks c
                                  WHERE c.document_id = d.id
                              )
                            """
                        ),
                        {"p": created_project},
                    )
                ).all()
            )

            failed_records = list(
                (
                    await db.execute(
                        text(
                            "SELECT id, status FROM documents "
                            "WHERE project_id = :p"
                        ),
                        {"p": created_project},
                    )
                ).all()
            )

        record(
            "没有留下「有内容却看起来正常、实际检索不到」的静默死档",
            suspicious == [],
            f"suspicious={suspicious}",
        )
        record(
            "失败留下了可见的 failed 记录（可被用户删除）",
            len(failed_records) == 1
            and failed_records[0][1] == "failed",
            f"records={failed_records}",
        )

        if created_project:
            await client.delete(f"/projects/{created_project}")
            print(f"      已清理项目 {created_project}")

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
