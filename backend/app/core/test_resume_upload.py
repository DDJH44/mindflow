"""验证上传全链路：可选文本 PDF 能上传、图片型 DOCX 被明确拒绝、失败不留残留。

背景（D55）：此前 `DocumentParser` 只支持 TXT/MD，而资料页写着
"支持 TXT / MD / PDF / DOCX"。**真实用户上传简历两次（PDF 738KB、
DOCX 811KB）都被拒**，库里还留下两条 `status=failed` 记录。

因此这里要验证的不只是"PDF 能传"，还包括：
- 解析出的文本可用（清洗规则的精确断言在 `test_document_parsing`）
- 图片型文档被**明确拒绝**，而不是静默产生空文档
- 上传失败**不留下任何记录与文件**

需要：Docker（PostgreSQL + Milvus）、后端 8000、Vite 5173、嵌入服务。
样本由 `app.core.make_test_fixtures` 生成。

用法：uv run python -m app.core.test_resume_upload
"""

import asyncio
import sys
import uuid
from pathlib import Path

import httpx

BASE = "http://127.0.0.1:5173/api"
ACCOUNT = "mindflow"
UPLOAD_DIR = Path(r"D:\yuxi\MindFlow\backend\uploads\documents")

FIXTURES = Path(__file__).parent / "fixtures"
PDF_PATH = FIXTURES / "text_resume.pdf"
DOCX_PATH = FIXTURES / "image_only.docx"

results: list[tuple[str, bool]] = []


def record(label: str, ok: bool, extra: str = "") -> None:
    results.append((label, ok))
    suffix = f"  {extra}" if extra else ""
    print(f"  [{'OK  ' if ok else 'FAIL'}] {label}{suffix}")


async def main() -> int:
    sys.path.insert(0, r"D:\yuxi\MindFlow\backend")

    from sqlalchemy import text as sql_text

    from app.core.security import create_access_token
    from app.core.test_support import wait_for_document_indexed
    from app.database.session import AsyncSessionLocal
    from app.services.retrieval_service import RetrievalService

    if not PDF_PATH.exists() or not DOCX_PATH.exists():
        print("  样本缺失，先运行：")
        print("  uv run python -m app.core.make_test_fixtures")
        return 1

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

    project_id: int | None = None
    document_id: int | None = None

    async with httpx.AsyncClient(
        base_url=BASE,
        headers={"Authorization": f"Bearer {token}"},
        timeout=900.0,
    ) as client:
        # try/finally 保证清理一定执行。
        #
        # **中途失败也必须清理** —— 否则每失败一次就在库里留下一个
        # 空项目（实测留下过两个：样本缺失导致提前退出，没走到清理段）。
        try:
            # ================================================
            print()
            print("=" * 74)
            print("1. 建项目并上传可选文本 PDF")
            print("=" * 74)

            response = await client.post(
                "/projects",
                json={"name": f"简历上传验证 {uuid.uuid4().hex[:6]}"},
            )
            project_id = response.json()["id"]
            record("建项目成功", response.status_code == 201)

            with open(PDF_PATH, "rb") as handle:
                response = await client.post(
                    f"/projects/{project_id}/documents",
                    params={"document_type": "resume"},
                    files={
                        "file": (
                            "text_resume.pdf",
                            handle.read(),
                            "application/pdf",
                        )
                    },
                )

            record(
                "PDF 上传返回 202（此前是「暂不支持的文件类型」）",
                response.status_code == 202,
                f"status={response.status_code}",
            )

            if response.status_code != 202:
                print("      响应:", response.text[:400])

            document = (
                response.json() if response.status_code == 202 else {}
            )
            document_id = document.get("id")

            if document_id:
                # 上传是异步的（§28）：202 时状态是 chunked，
                # 嵌入由后台 worker 完成。
                record(
                    "上传时状态为 chunked（已切块·待索引）",
                    document.get("status") == "chunked",
                    f"status={document.get('status')}",
                )

                final = await wait_for_document_indexed(
                    client, project_id, document_id
                )

                record(
                    "后台 worker 把它索引完成（embedded）",
                    final == "embedded",
                    f"最终状态={final}",
                )

            # ================================================
            print()
            print("=" * 74)
            print("2. 解析与切块落库")
            print("=" * 74)

            content = None
            if document_id:
                async with AsyncSessionLocal() as db:
                    content = (
                        await db.execute(
                            sql_text(
                                "SELECT content FROM documents "
                                "WHERE id = :d"
                            ),
                            {"d": document_id},
                        )
                    ).scalar_one_or_none()

                    chunk_count = (
                        await db.execute(
                            sql_text(
                                "SELECT COUNT(*) FROM document_chunks "
                                "WHERE document_id = :d"
                            ),
                            {"d": document_id},
                        )
                    ).scalar_one()

                record(
                    "正文已入库",
                    bool(content),
                    f"{len(content or '')} 字符",
                )
                record("已切块", chunk_count > 0, f"{chunk_count} 段")

                # 只断言**链路**属性。
                #
                # 清洗质量（汉字间空格是否清掉、英文词间空格是否保留）
                # 属于解析器逻辑，由 `test_document_parsing` 用确定性输入
                # 精确断言。在这里依赖样本内容会让测试与样本耦合 ——
                # 换一份样本就误报（这一点已实际踩到）。
                record(
                    "正文无零宽字符",
                    "\u200b" not in (content or ""),
                )

                print("      --- 正文 ---")
                for line in (content or "")[:200].split("\n"):
                    print(f"      {line}")

            # ================================================
            print()
            print("=" * 74)
            print("3. 检索能否命中（面试出题依赖它）")
            print("=" * 74)

            if document_id and project_id:
                async with AsyncSessionLocal() as db:
                    chunks = await RetrievalService(db).retrieve(
                        query="University of Finance",
                        project_id=project_id,
                        limit=5,
                    )

                record(
                    "检索命中该文档",
                    any(
                        chunk.get("document_id") == document_id
                        for chunk in chunks
                    ),
                    f"命中 {len(chunks)} 段",
                )

            # ================================================
            print()
            print("=" * 74)
            print("4. 图片型 DOCX 应被明确拒绝（而不是静默空文档）")
            print("=" * 74)

            with open(DOCX_PATH, "rb") as handle:
                response = await client.post(
                    f"/projects/{project_id}/documents",
                    params={"document_type": "resume"},
                    files={
                        "file": (
                            "image_only.docx",
                            handle.read(),
                            "application/vnd.openxmlformats-"
                            "officedocument.wordprocessingml.document",
                        )
                    },
                )

            record(
                "图片型 DOCX 返回 400",
                response.status_code == 400,
                f"status={response.status_code}",
            )

            detail = ""
            try:
                detail = response.json().get("detail", "")
            except Exception:  # noqa: BLE001
                pass

            record(
                "错误信息说明了原因与做法",
                "图片" in detail and "TXT" in detail,
                f"detail={detail[:60]}…" if detail else "",
            )

            # ================================================
            print()
            print("=" * 74)
            print("5. 上传失败不应留下记录或文件")
            print("=" * 74)

            async with AsyncSessionLocal() as db:
                failed_count = (
                    await db.execute(
                        sql_text(
                            "SELECT COUNT(*) FROM documents "
                            "WHERE project_id = :p "
                            "AND status = 'failed'"
                        ),
                        {"p": project_id},
                    )
                ).scalar_one()

            record(
                "图片型 DOCX 失败后没有 failed 记录",
                failed_count == 0,
                f"failed={failed_count}",
            )

            before = (
                set(UPLOAD_DIR.iterdir())
                if UPLOAD_DIR.exists()
                else set()
            )

            response = await client.post(
                f"/projects/{project_id}/documents",
                params={"document_type": "other"},
                files={
                    "file": (
                        "something.xlsx",
                        b"fake xlsx content",
                        "application/vnd.ms-excel",
                    )
                },
            )

            after = (
                set(UPLOAD_DIR.iterdir())
                if UPLOAD_DIR.exists()
                else set()
            )

            record(
                "不支持的类型返回 400",
                response.status_code == 400,
                f"status={response.status_code}",
            )
            record(
                "不支持的类型没有落盘任何文件",
                before == after,
                f"新增 {len(after - before)} 个文件",
            )

        finally:
            # ---------------- 清理 ----------------
            print()
            print("=" * 74)
            print("6. 清理")
            print("=" * 74)

            if project_id is not None:
                try:
                    response = await client.delete(
                        f"/projects/{project_id}"
                    )
                    record(
                        "删除验证项目",
                        response.status_code == 204,
                        f"status={response.status_code}",
                    )
                except Exception as exc:  # noqa: BLE001
                    record("删除验证项目", False, f"{exc}")

    # 汇总
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
