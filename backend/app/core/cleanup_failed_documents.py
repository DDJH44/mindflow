"""清理用户上传失败留下的文档记录与磁盘文件。

背景：`upload_document` 先建记录再解析，解析失败时记录会以
`status=failed` 留下、磁盘文件也不删。用户看到的是"上传失败"
但界面上会多出一条"处理失败"的记录，而它已经没用了。

本脚本只为这两条历史记录做清理；**新代码不再产生这类残留**
（解析失败时仍会保留记录以便排查，但删除端点可正常清理）。

用法：uv run python -m app.core.cleanup_failed_documents
"""

import asyncio
import sys
from pathlib import Path

from sqlalchemy import text

from app.database.session import AsyncSessionLocal


async def main() -> int:
    async with AsyncSessionLocal() as db:
        rows = list(
            (
                await db.execute(
                    text(
                        "SELECT id, name, file_type, file_path, "
                        "project_id, created_at "
                        "FROM documents WHERE status = 'failed' "
                        "ORDER BY id"
                    )
                )
            ).all()
        )

        if not rows:
            print("  没有 failed 状态的文档")
            return 0

        print(f"  发现 {len(rows)} 条 failed 文档：")
        for row in rows:
            print(
                f"    doc {row[0]} | {row[2]} | {row[1]} "
                f"| project={row[4]} | {row[5]}"
            )

        for row in rows:
            document_id = row[0]
            file_path = row[3]

            # chunk 由外键级联删除
            await db.execute(
                text("DELETE FROM documents WHERE id = :d"),
                {"d": document_id},
            )

            if file_path:
                path = Path(file_path)
                if not path.is_absolute():
                    path = Path.cwd() / path
                try:
                    if path.exists():
                        path.unlink()
                        print(f"    已删除文件 {path.name}")
                except OSError as exc:
                    print(f"    文件删除失败 {path}: {exc}")

        await db.commit()

        remaining = (
            await db.execute(
                text(
                    "SELECT COUNT(*) FROM documents "
                    "WHERE status = 'failed'"
                )
            )
        ).scalar_one()

    print(f"  清理后 failed 文档数: {remaining}")
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
