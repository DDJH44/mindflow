"""清理磁盘上没有任何文档记录的**孤儿上传文件**。

## 它们从哪来

上传流程是"先落盘 → 再解析 → 再建记录"。任何一个环节失败，
或**文档被删除**时，磁盘文件都可能留下来：

| 来源 | 是否已处理 |
| --- | --- |
| 解析失败 | ✅ 已处理（`_discard_upload`）|
| 体积超限 | ✅ 已处理（本次新增）|
| **文档被删除** | ❌ **未处理 —— 这是主要来源** |
| 早期版本的残留 | ❌ 历史遗留 |

实测（2026-10-13）：磁盘 80 个文件、库只记录 10 个
→ **70 个孤儿、5.44 MB**，其中含**真实简历**。

## 为什么必须处理，而不只是浪费磁盘

`.gitignore` 里写得很清楚：

> ⚠️ 这是**用户数据**，不是代码。实测这里躺着真实简历 PDF
> （含姓名、学校、专业、起止时间）。提交到公开仓库属于隐私泄露。

**一个用户以为已经删掉的文件，仍然留在磁盘上** ——
这不只是空间问题。

## 用法

    # 先看会删什么（默认行为，不改任何东西）
    uv run python -m scripts.cleanup_orphan_uploads

    # 真正删除
    uv run python -m scripts.cleanup_orphan_uploads --purge

    # 只清理超过 N 小时的文件（避免删掉"正在上传"的中间态）
    uv run python -m scripts.cleanup_orphan_uploads --purge --min-age-hours 6
"""

import argparse
import asyncio
import sys
import time
from pathlib import Path

from sqlalchemy import text

from app.database.session import AsyncSessionLocal

# 上传目录。
#
# ⚠️ 这里原本硬编码 `D:\yuxi\MindFlow\backend\uploads` ——
# 那在容器里会变成"目录不存在"，而本工具会**判定为没有孤儿**
# 并安静退出。也就是说：部署到 Linux 之后，这个清理工具
# 会一直"报告一切正常"，而实际什么都没检查。
#
# 这类"静默失效"比报错更危险 —— 报错会让人去查，
# 静默通过会让人以为已经清理过了。
#
# 改为相对本文件的包根推导：
#   scripts/cleanup_orphan_uploads.py → parents[2] = backend/
# 本地与容器（WORKDIR=/app）都成立。
UPLOAD_DIR = Path(__file__).resolve().parents[2] / "uploads"


async def collect() -> tuple[list[Path], list[Path]]:
    """返回 (孤儿文件, 全部文件)。"""

    async with AsyncSessionLocal() as db:
        rows = list(
            (
                await db.execute(
                    text("SELECT file_path FROM documents")
                )
            ).all()
        )

    # 只按**文件名**比对：数据库里存的是相对路径
    # （`uploads/documents/xxx.txt`），而工作目录可能不同。
    # 按 basename 比对对两者都成立。
    referenced = {
        Path(row[0]).name for row in rows if row[0]
    }

    all_files = [
        item
        for item in UPLOAD_DIR.rglob("*")
        if item.is_file()
    ] if UPLOAD_DIR.exists() else []

    orphans = [
        item for item in all_files if item.name not in referenced
    ]

    return orphans, all_files


async def main() -> int:
    parser = argparse.ArgumentParser(
        description="清理没有文档记录的孤儿上传文件",
    )
    parser.add_argument(
        "--purge",
        action="store_true",
        help="真正删除（默认只列出）",
    )
    parser.add_argument(
        "--min-age-hours",
        type=float,
        default=1.0,
        help=(
            "只清理修改时间早于 N 小时的文件（默认 1）。"
            "避免删掉「刚写完、记录还没提交」的中间态。"
        ),
    )
    args = parser.parse_args()

    print("=" * 76)
    print("孤儿上传文件清理")
    print("=" * 76)

    orphans, all_files = await collect()

    total_bytes = sum(item.stat().st_size for item in all_files)
    orphan_bytes = sum(item.stat().st_size for item in orphans)

    print(f"  上传目录   : {UPLOAD_DIR}")
    print(
        f"  磁盘文件   : {len(all_files)} 个 / "
        f"{round(total_bytes / 1024 / 1024, 2)} MB"
    )
    print(
        f"  孤儿文件   : {len(orphans)} 个 / "
        f"{round(orphan_bytes / 1024 / 1024, 2)} MB"
    )

    if not orphans:
        print()
        print("  ✓ 没有孤儿文件")
        return 0

    # 年龄过滤：避免删掉"刚落盘、记录还没提交"的文件。
    #
    # 这不是理论顾虑：上传流程是"先落盘再建记录"，
    # 两个动作之间有一个窗口。若清理正好在那时跑，
    # 就会删掉一个即将被引用的文件。
    cutoff = time.time() - args.min_age_hours * 3600

    fresh = [
        item for item in orphans if item.stat().st_mtime > cutoff
    ]
    aged = [
        item for item in orphans if item.stat().st_mtime <= cutoff
    ]

    if fresh:
        print()
        print(
            f"  ⏳ 跳过 {len(fresh)} 个「新」文件"
            f"（修改时间在 {args.min_age_hours} 小时内）——"
            "它们可能正在被引用"
        )

    print()
    print("  待清理（按体积降序，最多显示 15 个）:")

    for item in sorted(
        aged, key=lambda p: -p.stat().st_size
    )[:15]:
        age_hours = (time.time() - item.stat().st_mtime) / 3600
        print(
            f"    {item.stat().st_size:>9} 字节  "
            f"{age_hours:>6.1f} 小时前  {item.name}"
        )

    if len(aged) > 15:
        print(f"    … 其余 {len(aged) - 15} 个")

    if not args.purge:
        print()
        print(
            f"  （未删除 —— 加 --purge 才真正清理 "
            f"{len(aged)} 个文件）"
        )
        return 0

    print()
    print("  开始清理…")

    removed = 0
    failed = 0

    for item in aged:
        try:
            item.unlink()
            removed += 1
        except OSError as exc:
            failed += 1
            print(f"    ✗ {item.name}: {exc}")

    print()
    print(f"  已删除 {removed} 个（失败 {failed}）")
    print(
        f"  释放约 {round(orphan_bytes / 1024 / 1024, 2)} MB "
        f"（含未达年龄阈值而跳过的部分）"
    )

    if failed:
        return 1

    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
