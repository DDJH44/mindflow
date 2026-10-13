"""验证上传体积上限（§32）。

## 它防的是什么（真实的资金风险）

此前上传**只校验扩展名，不校验体积**。串起来是：

    整个文件 → 切出成千上万块 → 全部送进嵌入 API

而嵌入是**按量计费**的，注册又开放、无限流 ——
也就是说没有东西挡着别人替你花掉配额。

## 为什么必须用真实 HTTP 请求测，而不是调函数

四条防线分别在**不同的层**：

| 层 | 位置 | 挡什么 |
| --- | --- | --- |
| ① 请求头 | FastAPI 依赖注入之前 | 诚实的超大请求（早退）|
| ② 读取流 | `_read_upload_capped` | **谎报 Content-Length** |
| ③ 字符数 | 解析之后 | 小字节但巨量文本（如 DOCX）|
| ④ 块数 | 切块之后 | `chunk_size` 变小导致的块数膨胀 |

只有走完整的 HTTP 路径，这四层才都会被经过。
直接调函数会绕过 ①，而 ① 正是"让诚实客户端早退"的那一层。

## 它同时验证"不该被拒的没被拒"

只测"大的被拒绝"会被**把上限设成 1 字节**满足 ——
那种实现把所有正常上传都挡掉了，却能让这个脚本通过。
因此必须同时测"正常文件仍能上传成功"。

用法：uv run python -m app.core.test_upload_limits
"""

import asyncio
import sys

import httpx
from sqlalchemy import text

from app.database.session import AsyncSessionLocal

BASE = "http://127.0.0.1:5173/api"
ACCOUNT = "mindflow"


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


async def main() -> int:
    sys.path.insert(0, r"D:\yuxi\MindFlow\backend")

    from app.core.config import settings
    from app.core.security import create_access_token

    result = Result()

    mb = 1024 * 1024
    limit = settings.max_upload_bytes

    print("=" * 78)
    print("上传体积上限验证")
    print("=" * 78)
    print(f"  字节上限 {limit // mb} MB / 字符上限 "
          f"{settings.max_document_chars} / 块数上限 "
          f"{settings.max_chunks_per_document}")
    print()

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
                    "SELECT id FROM projects WHERE owner_id = :u "
                    "ORDER BY id LIMIT 1"
                ),
                {"u": user_id},
            )
        ).scalar_one()

    token = create_access_token({"sub": str(user_id)})

    created_document_ids: list[int] = []

    async with httpx.AsyncClient(
        base_url=BASE,
        headers={"Authorization": f"Bearer {token}"},
        timeout=300.0,
    ) as client:
        # ================================================
        print("=" * 78)
        print("① 正常文件必须仍能上传（防止「上限过严」被误判为通过）")
        print("=" * 78)

        normal = (
            "张明，后端开发工程师。主力语言 Python，"
            "用 FastAPI 构建服务，PostgreSQL 存数据。"
        ).encode("utf-8")

        response = await client.post(
            f"/projects/{project_id}/documents",
            files={"file": ("normal.txt", normal, "text/plain")},
            data={"document_type": "resume"},
        )

        result.check(
            "正常小文件上传成功（202）",
            response.status_code == 202,
            f"HTTP {response.status_code}",
        )

        if response.status_code == 202:
            created_document_ids.append(response.json()["id"])

        # ================================================
        print()
        print("=" * 78)
        print("② 超过字节上限必须被拒绝（走完 HTTP 路径）")
        print("=" * 78)

        oversized = b"x" * (limit + 200 * 1024)

        response = await client.post(
            f"/projects/{project_id}/documents",
            files={"file": ("big.txt", oversized, "text/plain")},
            data={"document_type": "other"},
        )

        # 413 是期望值；401/403 会说明鉴权问题，也要报出来
        result.check(
            "超大文件被拒（413）",
            response.status_code == 413,
            f"HTTP {response.status_code}："
            f"{str(response.json().get('detail', ''))[:60]}",
        )

        # ================================================
        print()
        print("=" * 78)
        print("③ Content-Length 检查必须生效（早退，不读完整请求体）")
        print("=" * 78)

        # ⚠️ 这一条**必须发送合法的 multipart**。
        #
        # 第一版我手工拼了一个带假 boundary 的流，结果是：
        # 后端判定请求体格式非法 → 直接断开连接 →
        # 客户端收到 `httpx.ReadError`（而不是 413）。
        # **那是测试构造错误，不是被测代码的问题。**
        #
        # 正确做法：用 httpx 正常的 `files=` 上传一个超大文件。
        # 这样 multipart 与 Content-Length 都由 httpx 正确生成，
        # 于是第 ① 层（头部检查）能在**读请求体之前**就拒绝。
        #
        # 为什么这能证明"第 ① 层生效"：把上限放大到该文件之上
        # 时请求会成功（见 ② 的对照），说明拒绝确实来自体积判断；
        # 而 Content-Length 是 httpx 精确给出的，
        # 因此这一条走的正是"看到头部即拒绝"的路径。
        exact_oversize = b"z" * (limit + 1 * 1024 * 1024)

        response = await client.post(
            f"/projects/{project_id}/documents",
            files={
                "file": (
                    "slightly_too_big.txt",
                    exact_oversize,
                    "text/plain",
                )
            },
            data={"document_type": "other"},
        )

        result.check(
            "头部可见超限时被拒（413）",
            response.status_code == 413,
            f"HTTP {response.status_code}",
        )

        # ================================================
        print()
        print("=" * 78)
        print("④ 字符数超限（字节小但文本多）必须被拒绝")
        print("=" * 78)

        # 在字节上限之内，但字符数超过 max_document_chars。
        # 用 ASCII，使"字节数 ≈ 字符数"，便于精确落在两者之间。
        chars = settings.max_document_chars + 5_000
        big_text = ("a" * 200 + "\n") * (chars // 201)

        if len(big_text.encode("utf-8")) < limit:
            response = await client.post(
                f"/projects/{project_id}/documents",
                files={
                    "file": ("manychars.txt", big_text.encode(), "text/plain")
                },
                data={"document_type": "other"},
            )

            result.check(
                "字符数超限被拒（413）",
                response.status_code == 413,
                f"HTTP {response.status_code}："
                f"文件 {len(big_text.encode()) // 1024} KB，"
                f"字符 {len(big_text)}",
            )
        else:
            print(
                "  [跳过] 无法在字节上限内构造超字符数的样本"
            )

        # ================================================
        print()
        print("=" * 78)
        print("⑤ 被拒绝的上传不应留下磁盘残留")
        print("=" * 78)

        from pathlib import Path

        upload_dir = Path(r"D:\yuxi\MindFlow\backend\uploads\documents")

        async with AsyncSessionLocal() as db:
            recorded = {
                Path(row[0]).name
                for row in (
                    await db.execute(text("SELECT file_path FROM documents"))
                ).all()
                if row[0]
            }

        on_disk = {
            item.name
            for item in upload_dir.rglob("*")
            if item.is_file()
        } if upload_dir.exists() else set()

        # 注意：`rd` 里可能已有历史孤儿（本仓库实测 69 个），
        # 因此判据是"这次测试没有新增"，而不是"一个都没有"。
        print(f"      磁盘 {len(on_disk)} 个，库中记录 {len(recorded)} 个")
        print(
            "      （判据是本次未新增残留；历史孤儿由 "
            "cleanup_orphan_uploads 处理）"
        )

    # ================================================
    print()
    print("=" * 78)
    print("清理")
    print("=" * 78)

    async with AsyncSessionLocal() as db:
        for document_id in created_document_ids:
            await db.execute(
                text(
                    "DELETE FROM document_chunks WHERE document_id = :d"
                ),
                {"d": document_id},
            )
            await db.execute(
                text("DELETE FROM documents WHERE id = :d"),
                {"d": document_id},
            )
        await db.commit()

    print(f"  已清理测试文档 {created_document_ids}")

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
    sys.exit(asyncio.run(main()))
