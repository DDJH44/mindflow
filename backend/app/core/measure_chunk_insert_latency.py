"""测量写入延迟（用于对照 N+1 优化前后的实际收益）。

## 为什么光有查询次数不够

"501 次查询 → 1 次"是有力的证据，但它是**代理指标**。
用户感知的是**时间**，而每个往返的耗时取决于网络与数据库负载。

因此这里测实际墙钟时间，按块数给出：

    块数    优化前      优化后
    100     ? ms       ? ms
    500     ? ms       ? ms

## 它怎么测"优化前"

优化已经把代码改掉了，因此无法直接跑旧代码。
本脚本用**等价的方式**复现旧行为：写入后再逐个 `refresh` 一次，
然后对比"不 refresh"的耗时。两者的差就是那 N 次往返的代价。

这不是"完美复刻"，但它测的正是那 N 次往返本身 ——
而那是这次优化唯一动掉的东西。

用法：uv run python -m app.core.measure_chunk_insert_latency
"""

import asyncio
import statistics
import sys
import time

from sqlalchemy import text

from app.core.cleanup_test_projects import TEST_PROJECT_PREFIX
from app.database.session import AsyncSessionLocal


async def prepare(chunk_count: int) -> tuple[int, int]:
    """建项目 + 文档，返回 (project_id, document_id)。"""

    async with AsyncSessionLocal() as db:
        owner_id = (
            await db.execute(
                text("SELECT id FROM users WHERE username=:u"),
                {"u": "mindflow"},
            )
        ).scalar_one()

        project_id = (
            await db.execute(
                text(
                    "INSERT INTO projects (name, owner_id, status, "
                    "created_at, updated_at) "
                    "VALUES (:name, :o, 'active', now(), now()) "
                    "RETURNING id"
                ),
                {
                    # 统一测试前缀 —— 见 `cleanup_test_projects`。
                    # 早期版本用中文名，而清理有 bug 时这些项目
                    # 会留在库里并直接显示在用户界面上。
                    "name": f"{TEST_PROJECT_PREFIX}latency-measure",
                    "o": owner_id,
                },
            )
        ).scalar_one()

        document_id = (
            await db.execute(
                text(
                    "INSERT INTO documents (name, original_filename, "
                    "file_type, project_id, document_type, status, "
                    "content, created_at, updated_at) "
                    "VALUES ('l1', 'l1.txt', 'txt', :p, 'other', "
                    "'parsed', 'x', now(), now()) RETURNING id"
                ),
                {"p": project_id},
            )
        ).scalar_one()

        await db.commit()

    return project_id, document_id


async def cleanup(project_id: int, document_id: int) -> None:
    async with AsyncSessionLocal() as db:
        await db.execute(
            text("DELETE FROM document_chunks WHERE document_id=:d"),
            {"d": document_id},
        )
        await db.execute(
            text("DELETE FROM documents WHERE id=:d"), {"d": document_id}
        )
        await db.execute(
            text("DELETE FROM projects WHERE id=:p"), {"p": project_id}
        )
        await db.commit()


async def one_run(
    chunk_count: int,
    with_refresh: bool,
    rounds: int = 3,
) -> float:
    """返回中位耗时（毫秒）。"""

    from app.repositories.document_chunk_repository import (
        DocumentChunkRepository,
    )

    samples: list[float] = []

    for _ in range(rounds):
        project_id, document_id = await prepare(chunk_count)

        try:
            async with AsyncSessionLocal() as db:
                repository = DocumentChunkRepository(db)

                started = time.perf_counter()

                created = await repository.create_chunks(
                    document_id=document_id,
                    chunks=[
                        f"第 {i} 段内容。" * 8
                        for i in range(chunk_count)
                    ],
                    chunk_metadata={"document_type": "other"},
                )

                if with_refresh:
                    # 复现旧行为：逐个 refresh
                    for chunk in created:
                        await db.refresh(chunk)

                samples.append(
                    (time.perf_counter() - started) * 1000
                )

        finally:
            await cleanup(project_id, document_id)

    return statistics.median(samples)


async def main() -> int:
    sys.path.insert(0, r"D:\yuxi\MindFlow\backend")

    print("=" * 78)
    print("切块写入延迟测量")
    print("=" * 78)
    print()
    print("  （`with_refresh` 复现优化前的逐个 refresh 行为）")
    print()
    print(
        f"  {'块数':>6} {'优化前':>12} {'优化后':>12} "
        f"{'节省':>12} {'倍数':>8}"
    )

    for chunk_count in (100, 500, 1000):
        before = await one_run(chunk_count, with_refresh=True)
        after = await one_run(chunk_count, with_refresh=False)

        saved = before - after
        ratio = before / after if after else 0

        print(
            f"  {chunk_count:>6} {before:>10.1f}ms {after:>10.1f}ms "
            f"{saved:>10.1f}ms {ratio:>7.1f}x"
        )

    print()
    print("  说明：")
    print("    「优化前」= 写入 + 逐块 refresh（旧实现）")
    print("    「优化后」= 只写入（现实现）")
    print("    差值即那 N 次往返的代价 —— 而那是本次优化唯一动掉的东西。")

    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
