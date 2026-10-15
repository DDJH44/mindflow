"""验证去掉 `refresh` 后，`create_chunks` 的返回值仍然可用。

## 为什么要单独验证这一条

优化前 `create_chunks` 在 `commit()` 之后对每个块调用 `refresh()`。
我的判断是"那是多余的，因为 SQLAlchemy 在 INSERT 时已经用
`RETURNING` 取回了 `id`"。

**但这个判断必须验证，不能靠推理** —— 依据是：

1. PostgreSQL 的 INSERT 是否真的带 `RETURNING`（取决于方言与配置）
2. `expire_on_commit=False`（本项目已设）是否让对象在 commit 后
   仍保留属性值
3. 异步 ORM 里访问已过期属性会抛 `MissingGreenlet` ——
   而那是**运行时报错**，编译与类型检查都发现不了

第 3 条特别值得注意：本项目已经因为
"commit 之后访问 ORM 属性"踩过一次坑（D61 的 `current_user.id`）。
因此这里要**明确验证**每个块在 `create_chunks` 返回后
`id` 与 `created_at` 都可读，而且**不产生额外查询**。

用法：uv run python -m app.core.test_chunk_insert_efficiency
"""

import asyncio
import sys
from collections import Counter

from sqlalchemy import event, text

from app.database.session import AsyncSessionLocal, engine


class QueryCounter:
    """统计发往数据库的语句。

    ## ⚠️ 状态管理踩过一次坑（记下来）

    第一版用 `hasattr(self, "_handler")` 判断"要不要移除监听器"，
    而 `stop()` **没有清掉 `_handler`**。于是：

    - 步骤 ③ 结束时 `stop()` → 移除成功
    - `finally` 里再 `stop()` → **找不到监听器 → 抛
      `InvalidRequestError`**

    表现是"单独跑通过，但在门禁里失败" —— 因为门禁里
    `finally` 一定会执行，而单独跑时我先看到的是通过的那次。
    这类"一次成功一次失败"最容易让人误判成环境问题。

    正确做法：用**明确的布尔标志**跟踪"当前是否已挂载"，
    并在 `stop()` 里把它清掉。
    """

    def __init__(self) -> None:
        self.statements: list[str] = []
        self._handler = None

    def start(self) -> None:
        # 已挂载就先卸掉，避免重复挂载导致语句被计两次
        self.stop()

        @event.listens_for(engine.sync_engine, "before_cursor_execute")
        def _record(  # noqa: ANN001
            conn, cursor, statement, parameters, context, executemany
        ):
            self.statements.append(statement)

        self._handler = _record

    def stop(self) -> None:
        """移除监听器。**可安全重复调用。**"""

        if self._handler is None:
            return

        event.remove(
            engine.sync_engine, "before_cursor_execute", self._handler
        )

        # 必须清空 —— 否则第二次调用会去移除一个已经移除的监听器
        self._handler = None

    def kinds(self) -> Counter:
        counter: Counter = Counter()

        for statement in self.statements:
            head = statement.strip().split(None, 1)
            counter[head[0].upper() if head else "(空)"] += 1

        return counter


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

    from app.repositories.document_chunk_repository import (
        DocumentChunkRepository,
    )

    result = Result()

    print("=" * 78)
    print("切块写入效率与返回值可用性")
    print("=" * 78)

    # ---------------- 准备 ----------------
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
                    "VALUES ('切块效率验证', :o, 'active', now(), now()) "
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
                    "VALUES ('e1', 'e1.txt', 'txt', :p, 'other', "
                    "'parsed', 'x', now(), now()) RETURNING id"
                ),
                {"p": project_id},
            )
        ).scalar_one()

        await db.commit()

    chunk_count = 100
    counter = QueryCounter()

    try:
        # ================================================
        print()
        print("=" * 78)
        print(f"① 写入 {chunk_count} 块的查询次数")
        print("=" * 78)

        returned = []

        counter.start()

        async with AsyncSessionLocal() as db:
            returned = await DocumentChunkRepository(
                db
            ).create_chunks(
                document_id=document_id,
                chunks=[f"第 {i} 段。" * 8 for i in range(chunk_count)],
                chunk_metadata={"document_type": "other"},
            )

        counter.stop()

        kinds = counter.kinds()
        total = len(counter.statements)

        print(f"      总查询 {total}，构成 {dict(kinds)}")

        # 判据：不应随块数线性增长。
        #
        # 允许"常数级"的开销（BEGIN/COMMIT/取 id 各一次），
        # 但**不允许**每个块一次往返。
        result.check(
            "查询次数不随块数线性增长（无 N+1）",
            total < chunk_count,
            f"{chunk_count} 块只用了 {total} 次查询"
            if total < chunk_count
            else f"{total} 次查询 ≈ 块数，仍是 N+1",
        )

        # ================================================
        print()
        print("=" * 78)
        print("② 返回值仍然可用（这是去掉 refresh 的**前提**）")
        print("=" * 78)

        result.check(
            f"返回了 {chunk_count} 个块",
            len(returned) == chunk_count,
            f"实得 {len(returned)}",
        )

        # id 必须已回填 —— 若有调用方要用它（例如写向量时以 id 为主键）
        missing_ids = [c for c in returned if c.id is None]

        result.check(
            "**每个块的 id 都已回填**（不靠额外查询）",
            not missing_ids,
            f"{len(missing_ids)} 个块没有 id"
            if missing_ids
            else f"例如 id={returned[0].id} … {returned[-1].id}",
        )

        # created_at 是 Python 侧默认值，也应当已设置
        missing_created = [
            c for c in returned if c.created_at is None
        ]

        result.check(
            "每个块的 created_at 都已设置",
            not missing_created,
            f"{len(missing_created)} 个缺失"
            if missing_created
            else f"例如 {returned[0].created_at}",
        )

        # content / chunk_index 必须与输入一致（防止批量插入串位）
        result.check(
            "chunk_index 是 0..n-1 且有序",
            [c.chunk_index for c in returned]
            == list(range(chunk_count)),
        )

        result.check(
            "content 与输入一致（没有串位）",
            returned[0].content.startswith("第 0 段。")
            and returned[-1].content.startswith("第 99 段。"),
        )

        # ================================================
        print()
        print("=" * 78)
        print("③ 返回值可用性不产生额外查询")
        print("=" * 78)

        # 访问属性**不应**触发懒加载。
        #
        # 这是异步 ORM 的经典坑：过期属性一旦被访问会发起 IO，
        # 在同步上下文里抛 `MissingGreenlet` —— 编译与类型检查
        # 都发现不了，只有真的访问才知道。
        counter.statements = []

        counter.start()
        _ = [c.id for c in returned]
        _ = [c.created_at for c in returned]
        _ = [c.embedding_status for c in returned]
        counter.stop()

        result.check(
            "读取 id / created_at / embedding_status 不触发查询",
            not counter.statements,
            f"产生了 {len(counter.statements)} 次查询"
            if counter.statements
            else "零查询（对象仍已加载）",
        )

        # embedding_status 的 Python 侧默认值也应当在
        result.check(
            "embedding_status 默认值为 pending",
            all(c.embedding_status == "pending" for c in returned),
        )

        # ================================================
        print()
        print("=" * 78)
        print("④ 数据确实落库了（不是只在内存里）")
        print("=" * 78)

        async with AsyncSessionLocal() as db:
            stored = (
                await db.execute(
                    text(
                        "SELECT count(*) FROM document_chunks "
                        "WHERE document_id=:d"
                    ),
                    {"d": document_id},
                )
            ).scalar_one()

        result.check(
            "库中的块数与写入一致",
            stored == chunk_count,
            f"库中 {stored} 块",
        )

    finally:
        counter.stop()

        async with AsyncSessionLocal() as db:
            await db.execute(
                text("DELETE FROM document_chunks WHERE document_id=:d"),
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

        print()
        print("  已清理测试数据")

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
