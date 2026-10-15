"""测量切块写入的查询次数（N+1 的证据与优化后的对照）。

## 为什么要先测

计划书里那条待办写着"实测仍在：`document_chunk_repository.py:88`" ——
但那只是**读代码得出的判断**，没有查询计数。

而"看起来像 N+1"与"确实是 N+1"是两件事：
- 若 `commit()` 之后 ORM 已经把需要的列取回了，那 `refresh` 是多余的
- 若 `refresh` 确实每次都发一条 SELECT，那是真的 N+1

**因此先数查询，再决定改不改。**

## 它测什么

用 SQLAlchemy 的 `before_cursor_execute` 事件**逐条统计**
实际发给数据库的语句，按块数给出：

| 块数 | 查询次数 |
| --- | --- |
| 10 | ? |
| 100 | ? |
| 500 | ? |

以及**构成**（几条 INSERT、几条 SELECT）——
只看总数看不出是哪种 N+1。

用法：uv run python -m app.core.measure_chunk_insert_queries
"""

import asyncio
import re
import sys
from collections import Counter

from sqlalchemy import event, text

from app.database.session import AsyncSessionLocal, engine


class QueryCounter:
    """统计发往数据库的语句。"""

    def __init__(self) -> None:
        self.statements: list[str] = []
        self._listening = False

    def start(self) -> None:
        if self._listening:
            return

        @event.listens_for(engine.sync_engine, "before_cursor_execute")
        def _record(  # noqa: ANN001
            conn, cursor, statement, parameters, context, executemany
        ):
            self.statements.append(statement)

        self._handler = _record
        self._listening = True

    def stop(self) -> None:
        if not self._listening:
            return

        event.remove(
            engine.sync_engine, "before_cursor_execute", self._handler
        )
        self._listening = False

    def reset(self) -> None:
        self.statements = []

    def summary(self) -> Counter:
        """按语句类型归类。

        取每条语句的第一个关键字（INSERT / SELECT / UPDATE …）。
        """

        kinds: Counter = Counter()

        for statement in self.statements:
            head = statement.strip().split(None, 1)

            if not head:
                kinds["(空)"] += 1
                continue

            kinds[head[0].upper()] += 1

        return kinds


async def measure(chunk_count: int) -> tuple[int, Counter]:
    """返回 (总查询次数, 分类统计)。"""

    from app.core.security import hash_password  # noqa: F401
    from app.repositories.document_chunk_repository import (
        DocumentChunkRepository,
    )

    counter = QueryCounter()

    # 先准备一次项目与文档（不计入统计）
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
                    "VALUES ('N+1 测量', :o, 'active', now(), now()) "
                    "RETURNING id"
                ),
                {"o": owner_id},
            )
        ).scalar_one()

        document_id = (
            await db.execute(
                text(
                    "INSERT INTO documents (name, original_filename, "
                    "file_type, project_id, document_type, status, "
                    "content, created_at, updated_at) "
                    "VALUES ('n1', 'n1.txt', 'txt', :p, 'other', "
                    "'parsed', 'x', now(), now()) RETURNING id"
                ),
                {"p": project_id},
            )
        ).scalar_one()

        await db.commit()

    try:
        counter.start()
        counter.reset()

        async with AsyncSessionLocal() as db:
            repository = DocumentChunkRepository(db)

            await repository.create_chunks(
                document_id=document_id,
                chunks=[
                    f"第 {index} 段内容。" * 8
                    for index in range(chunk_count)
                ],
                chunk_metadata={"document_type": "other"},
            )

        counter.stop()

    finally:
        # 清理
        async with AsyncSessionLocal() as db:
            await db.execute(
                text(
                    "DELETE FROM document_chunks WHERE document_id=:d"
                ),
                {"d": document_id},
            )
            await db.execute(
                text("DELETE FROM documents WHERE id=:d"),
                {"d": document_id},
            )
            await db.execute(
                text("DELETE FROM projects WHERE id=:p"),
                {"p": project_id},
            )
            await db.commit()

    return len(counter.statements), counter.summary()


async def main() -> int:
    sys.path.insert(0, r"D:\yuxi\MindFlow\backend")

    print("=" * 76)
    print("切块写入的查询次数测量")
    print("=" * 76)
    print()
    print(
        f"  {'块数':>6} {'总查询':>8} {'构成':<40}"
    )

    for chunk_count in (10, 100, 500):
        total, kinds = await measure(chunk_count)

        composition = "  ".join(
            f"{name}={count}" for name, count in kinds.most_common()
        )

        print(f"  {chunk_count:>6} {total:>8} {composition:<40}")

    print()
    print("  判读：")
    print("    若总查询 ≈ 块数 + 常数 → 每个块一次往返（真 N+1）")
    print("    若总查询 ≈ 常数      → 批量写入，无 N+1")

    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
