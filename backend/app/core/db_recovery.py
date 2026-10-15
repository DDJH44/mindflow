"""数据库连接失效的检测与恢复（§36）。

## 要解决的问题

实测日志（Docker 栈停了 10 小时后）：

```text
worker 循环异常：asyncpg.InterfaceError: connection is closed
```

它发生在**租约恢复的 UPDATE** 上。`run_forever` 捕获了异常因此
worker 没死 —— 但**那一次租约恢复被跳过了**，
而租约恢复正是**崩溃恢复所依赖的机制**。

也就是说：**最需要它的时刻，它失效了。**

## 三个层次的问题，分开修

| 问题 | 表现 | 修法 |
| --- | --- | --- |
| 单个连接过期 | 取出来的连接已死 | `pool_pre_ping=True`（已在 `session.py` 配置）|
| 连接活了太久 | 服务器单方面关闭 | `pool_recycle=3600`（同上）|
| **整个池子的链路断了** | 连探活都失败 | **`dispose()` 重建池子** ← 本模块 |

前两条是"用之前检查"，解决不了第三条：Docker 网络被重建时，
池子里**所有**连接都失效了，逐个探活是浪费，
正确做法是**整池丢弃**。

## 怎么判断"该重建池子"，而不是靠猜错误信息

**不要用字符串匹配。** 那种写法会随驱动版本变化而失效，
而且会漏掉没预料到的措辞 —— 而失效的方式是**静默的**
（该重建时没重建，下次请求继续失败）。

SQLAlchemy 已经把这件事**标准化**了：

- `DBAPIError.connection_invalidated` → **连接已被作废**
  （驱动明确告知这个连接不能再用了）
- `DisconnectionError` → 从池里取连接时就失败

用这两个结构化信号，而不是 `"connection is closed" in str(exc)`。

## 用法

```python
from app.core.db_recovery import recover, with_recovery

# 方式一：包一段操作，失败时自动重建池子并重试一次
count = await with_recovery("租约恢复", self.recover_expired_leases)

# 方式二：手动判断并恢复
if await recover(exc):
    ...  # 池子已重建，可以重试
```
"""

from typing import Awaitable, Callable, TypeVar

from sqlalchemy.exc import DBAPIError, DisconnectionError, InterfaceError

from app.core.logging import get_logger

logger = get_logger("app.db_recovery")

T = TypeVar("T")

# 重建池子的次数上限（用于测试与观测）。
# 不去限制总量 —— 连接断开是外部事件，
# 限制"恢复次数"只会让长时间运行的服务在某个时刻停止自愈。
_recovery_count = 0


def is_connection_failure(exc: BaseException) -> bool:
    """判断异常是否表示"连接已失效"。

    ## 为什么用类型标志而不是错误文本

    字符串匹配（`"connection is closed" in str(exc)`）的问题：

    1. **随驱动版本变化**：asyncpg 与 psycopg 的措辞不同，
       同驱动不同版本的措辞也可能变
    2. **失效方式是静默的**：匹配不到时不会报错，
       只是"该重建池子时没重建"，于是下次请求继续失败 ——
       而日志里看不出少做了一件事
    3. **会误判**：一条恰好含这些词的业务异常会被当成连接故障

    SQLAlchemy 已经把这件事标准化：

    | 信号 | 含义 |
    | --- | --- |
    | `DBAPIError.connection_invalidated` | 驱动明确告知该连接已作废 |
    | `DisconnectionError` | 从池里取连接时失败 |
    | `InterfaceError` | 驱动层接口错误（连接处于不可用状态）|
    """

    # 结构化信号 —— 最可靠
    if isinstance(exc, DBAPIError) and exc.connection_invalidated:
        return True

    if isinstance(exc, (DisconnectionError, InterfaceError)):
        return True

    # asyncpg 的 InterfaceError 在 SQLAlchemy 里可能被包成
    # `DBAPIError` 而不带 `connection_invalidated`。
    # 此时看**原始驱动异常**的类型名 —— 仍然基于类型，
    # 而不是消息文本。
    original = getattr(exc, "orig", None)

    if original is not None:
        original_name = type(original).__name__

        if original_name in (
            "InterfaceError",
            "ConnectionDoesNotExistError",
            "ConnectionResetError",
            "CannotConnectNowError",
        ):
            return True

    return False


async def recover(exc: BaseException | None = None) -> bool:
    """若 `exc` 表示连接失效，则重建连接池。返回是否重建了。

    ## 为什么必须 dispose，而不是重试同一个连接

    `dispose()` 会**关掉池子里所有连接**，之后取用时会新建。
    这正是"链路断了"场景需要的 —— 逐个连接探活是浪费，
    而且探活本身也会失败。

    ## 为什么要等 `close=False`

    `engine.dispose()` 默认会**等所有借出的连接归还**。
    在 worker 里那可能永远等不到（有连接正被一个卡住的事务持有），
    从而让恢复本身挂死。

    `close=False` 表示"标记池子作废，但不等归还" ——
    这正是恢复场景要的语义：**我们确定那些连接已经没用了**。
    """

    global _recovery_count

    if exc is not None and not is_connection_failure(exc):
        return False

    from app.database.session import engine

    try:
        await engine.dispose()

        _recovery_count += 1

        logger.warning(
            "数据库连接池已重建（第 %d 次）。"
            "触发原因：%s: %s",
            _recovery_count,
            type(exc).__name__ if exc else "（主动）",
            str(exc)[:150] if exc else "",
        )

        return True

    except Exception as inner:  # noqa: BLE001
        # 重建失败也要记下来 —— 那说明数据库真的不可达，
        # 而"恢复动作本身失败"是需要人知道的。
        logger.error("重建连接池失败：%s: %s", type(inner).__name__, inner)
        return False


async def with_recovery(
    operation: str,
    func: Callable[[], Awaitable[T]],
) -> T:
    """执行 `func`；若因连接失效而失败，则重建池子并**重试一次**。

    ## 为什么只重试一次

    连接失效是**外部事件**（Docker 重启、网络抖动）。
    重建池子后重试一次，如果还失败，说明数据库**真的不可达** ——
    那时继续重试只是把"数据库挂了"伪装成"偶尔失败"，
    并让日志里堆满重复错误。

    因此：**重试一次，然后把失败如实暴露出去。**

    ## 为什么把操作名传进来

    日志要能回答"是哪个操作失败了"。
    `with_recovery("租约恢复", ...)` 比 `with_recovery(fn)` 更有用 ——
    后者只能报出函数名，而调用点的语义往往更清楚。
    """

    try:
        return await func()

    except Exception as exc:  # noqa: BLE001
        if not is_connection_failure(exc):
            # 不是连接问题 → 原样抛出，不掩盖真实缺陷
            raise

        logger.warning(
            "%s 因连接失效失败，重建连接池后重试一次：%s: %s",
            operation,
            type(exc).__name__,
            str(exc)[:150],
        )

        if not await recover(exc):
            raise

        # 重试。若这次仍失败，异常原样冒泡 ——
        # 说明问题不是"连接需要重建"。
        return await func()


def recovery_count() -> int:
    """已重建池子的次数。供测试与观测使用。"""

    return _recovery_count


def reset_recovery_count() -> None:
    """清零计数。**只给测试用。**"""

    global _recovery_count
    _recovery_count = 0
