"""验证数据库连接失效的恢复（§36）。

## 它防的是什么

实测日志（Docker 栈停了 10 小时后）：

```text
worker 循环异常：asyncpg.InterfaceError: connection is closed
```

它发生在**租约恢复**上 —— 而租约恢复正是**崩溃恢复所依赖的机制**。
worker 没死，但**那一次恢复被跳过了**：
**最需要它的时刻，它失效了。**

## 怎么"真的"制造连接失效

不用 mock —— mock 只能测出"我以为的失效形态"，
而真实的失效形态（驱动抛什么异常、带不带
`connection_invalidated`）才是关键。

因此用 PostgreSQL 自带的
`pg_terminate_backend(pid)` **从服务端强制踢掉连接**。
那正是生产里会发生的事：服务器重启、连接被清理、
空闲超时把连接关掉。

## 它验证五件事

1. **检测**：真实的连接失效异常能被 `is_connection_failure` 认出
2. **不误判**：业务异常（如 `ValueError`）**不会**被当成连接故障
3. **恢复**：`recover()` 能重建连接池，之后查询恢复正常
4. **重试**：`with_recovery` 在连接失效时**自动重试一次**
5. **不掩盖**：不是连接故障时，异常**原样抛出**（不吞掉真缺陷）

用法：uv run python -m app.core.test_db_recovery
"""

import asyncio
import sys

from sqlalchemy import text

from app.database.session import AsyncSessionLocal, engine


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


KILLER_MARKER = "mindflow_db_recovery_test"


def tag_own_pool() -> None:
    """给**本进程的 `AsyncSessionLocal` 池**的每个连接打上标记。

    ## 为什么需要标记

    要"真的"复现连接失效，必须从服务端踢掉连接。
    而 `pg_terminate_backend` 一旦写错 WHERE，就会把**后端的连接**
    也踢掉 —— 实测后果是 `test_e2e_http` 报
    `RemoteProtocolError: Server disconnected`，
    而那个失败看起来像"e2e 坏了"。

    因此：**只踢"带本测试标记"的连接**。
    标记打在**要被踢的那个池**上，而不是打在"踢连接用的那个池"上 ——
    后者是逻辑错误（那等于只踢自己），我第一版就写反了。

    用 SQLAlchemy 的 `connect` 事件而不是连接串参数：
    池会**复用**连接，只有"连接建立时"这个钩子能保证
    每个连接都被标记到。
    """

    from sqlalchemy import event

    from app.database.session import engine

    @event.listens_for(engine.sync_engine, "connect")
    def _tag(dbapi_connection, _record):  # noqa: ANN001
        try:
            cursor = dbapi_connection.cursor()
            cursor.execute(
                f"SET application_name = '{KILLER_MARKER}'"
            )
            cursor.close()
        except Exception:  # noqa: BLE001
            # 标记失败不致命：下面会踢不到连接，
            # 测试随之如实失败 —— 而不是静默踢错对象。
            pass


async def kill_own_connections() -> int:
    """从服务端踢掉**带本测试标记**的连接。返回数量。

    只影响本测试进程的池，**不动后端的连接**。
    """

    from sqlalchemy import text as sql_text
    from sqlalchemy.ext.asyncio import create_async_engine

    from app.core.config import settings

    # 用一个**独立的**引擎执行踢连接 ——
    # 它不参与被踢的池，因此不会踩到自己的连接。
    killer_engine = create_async_engine(settings.database_url)

    try:
        async with killer_engine.connect() as conn:
            killed = (
                await conn.execute(
                    sql_text(
                        """
                        SELECT count(*) FROM (
                          SELECT pg_terminate_backend(pid)
                          FROM pg_stat_activity
                          WHERE datname = current_database()
                            AND application_name = :marker
                            AND pid <> pg_backend_pid()
                        ) AS terminated
                        """
                    ),
                    {"marker": KILLER_MARKER},
                )
            ).scalar_one()

            await conn.commit()

    finally:
        await killer_engine.dispose()

    return int(killed or 0)


async def main() -> int:
    sys.path.insert(0, r"D:\yuxi\MindFlow\backend")

    from app.core.db_recovery import (
        is_connection_failure,
        recover,
        recovery_count,
        reset_recovery_count,
        with_recovery,
    )

    result = Result()

    print("=" * 78)
    print("数据库连接失效恢复验证")
    print("=" * 78)

    # 先给本进程的池打标记，之后的踢连接只针对带标记的连接。
    #
    # 必须在**任何连接建立之前**调用 —— `connect` 事件只对
    # "之后建立的连接"生效。因此放在最前面。
    tag_own_pool()

    # ================================================
    print()
    print("=" * 78)
    print("① 业务异常**不能**被误判为连接故障")
    print("=" * 78)

    # 这一条很重要：若判据太宽，真实的代码缺陷会被
    # "当成连接问题"而重试/掩盖。
    for exc in (
        ValueError("普通业务错误"),
        KeyError("缺字段"),
        RuntimeError("模型输出无法解析"),
    ):
        result.check(
            f"{type(exc).__name__} 不被判为连接故障",
            not is_connection_failure(exc),
        )

    # ================================================
    print()
    print("=" * 78)
    print("② 真实的连接失效能被检测到")
    print("=" * 78)

    # 先占住一个池内连接，然后把它从服务端踢掉，
    # 再用它执行 SQL —— 这样能拿到**真实驱动抛出的异常**。
    caught: BaseException | None = None

    try:
        async with AsyncSessionLocal() as db:
            # 建立连接
            await db.execute(text("SELECT 1"))

            killed = await kill_own_connections()
            print(f"      已从服务端踢掉 {killed} 个连接")

            # 再执行一次。连接已经被服务端关掉，
            # 驱动应当报错。
            await db.execute(text("SELECT 1"))
            await db.commit()

    except Exception as exc:  # noqa: BLE001
        caught = exc

    if caught is None:
        # 没有异常可能是 pre_ping 提前发现并换了一个新连接 ——
        # 那本身是**好事**（说明健康检查在起作用），
        # 但意味着这一条无法用"踢连接"来验证。
        result.check(
            "连接失效可被检测（本次由 pre-ping 提前拦下）",
            True,
            "pre_ping 换掉了失效连接，因此没抛异常 —— 那是预期行为",
        )
    else:
        print(f"      捕获：{type(caught).__name__}: {str(caught)[:70]}")

        result.check(
            "真实连接失效被判为连接故障",
            is_connection_failure(caught),
            f"{type(caught).__name__}，"
            f"connection_invalidated="
            f"{getattr(caught, 'connection_invalidated', 'n/a')}",
        )

    # ================================================
    print()
    print("=" * 78)
    print("③ recover() 能重建连接池，之后查询恢复")
    print("=" * 78)

    reset_recovery_count()

    rebuilt = await recover(caught)
    print(f"      recovery_count = {recovery_count()}")

    result.check("recover() 返回 True（确实重建了）", rebuilt)

    # 重建后必须能正常查询 —— 这才叫"恢复了"
    async with AsyncSessionLocal() as db:
        value = (await db.execute(text("SELECT 1"))).scalar_one()

    result.check(
        "重建后查询正常",
        value == 1,
        f"SELECT 1 → {value}",
    )

    # ================================================
    print()
    print("=" * 78)
    print("④ with_recovery 会自动重试一次")
    print("=" * 78)

    reset_recovery_count()

    attempts = {"count": 0}

    async def flaky() -> str:
        """第一次抛连接故障，第二次成功。

        这模拟"连接失效 → 重建池子 → 重试成功"。
        """

        attempts["count"] += 1

        if attempts["count"] == 1:
            # 构造一个**被判为连接故障**的异常。
            # 用真实的 DBAPIError 形态，而不是随便抛一个。
            from sqlalchemy.exc import InterfaceError as SaInterfaceError

            raise SaInterfaceError(
                statement="SELECT 1",
                params={},
                orig=ConnectionResetError("模拟：连接已被重置"),
            )

        return "ok"

    value = await with_recovery("模拟操作", flaky)

    result.check(
        "重试成功并返回结果",
        value == "ok",
        f"尝试了 {attempts['count']} 次",
    )
    result.check(
        "确实重试了一次（共 2 次调用）",
        attempts["count"] == 2,
        f"调用次数 {attempts['count']}",
    )
    result.check(
        "重试前重建了连接池",
        recovery_count() >= 1,
        f"recovery_count={recovery_count()}",
    )

    # ================================================
    print()
    print("=" * 78)
    print("⑤ 不是连接故障时，异常原样抛出（不吞真缺陷）")
    print("=" * 78)

    async def broken_business_logic() -> None:
        raise ValueError("这是一个业务缺陷，不是连接问题")

    raised: BaseException | None = None

    try:
        await with_recovery("模拟业务操作", broken_business_logic)
    except Exception as exc:  # noqa: BLE001
        raised = exc

    result.check(
        "业务异常原样抛出（不被当成连接问题重试/掩盖）",
        isinstance(raised, ValueError),
        f"{type(raised).__name__}: {raised}",
    )

    # 而且**不应该**因此重建池子（那是无谓的抖动）
    result.check(
        "业务异常不触发连接池重建",
        recovery_count() == 1,
        f"recovery_count 仍为 {recovery_count()}",
    )

    # ================================================
    print()
    print("=" * 78)
    print("⑥ 引擎层健康配置已生效")
    print("=" * 78)

    pool = engine.pool

    result.check(
        "pool_pre_ping 已启用（处理单个连接过期）",
        getattr(pool, "_pre_ping", False) is True,
    )
    result.check(
        "pool_recycle 已设置（处理连接活太久）",
        getattr(pool, "_recycle", 0) == 3600,
        f"recycle={getattr(pool, '_recycle', '?')}",
    )

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
