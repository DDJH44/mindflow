"""日志基础设施。

**为什么现在做**：代码里此前**完全没有 logging** —— 生产代码只有 5 处
`print`（在 LLM 服务与两个评价服务里）。排查问题时看不到
"哪个请求、哪个用户、哪一步"，只能靠复现。

**为什么不把验证脚本的 print 也改掉**：那些脚本的 `print` 是
**它们的用户界面**（人要看的结果）。改成 logging 反而要额外配置
才能看到输出。因此本项目有两条并行的约定：

| 位置 | 输出方式 | 理由 |
| --- | --- | --- |
| `app/core/*.py`（验证脚本）| `print` | 输出是给人看的结论 |
| `app/services` / `routers` | `logging` | 输出是给运维排查用的 |

**为什么要 request_id**：一次请求会经过路由 → 服务 → 仓储 →
外部调用，日志散在多处。没有关联 ID 时无法回答
"这个报错属于哪一次请求"。中间件生成它、写进日志、
并通过响应头返回给调用方 —— 用户报错时能直接给出这个 ID。

**设计取舍：不引入结构化日志库。** 当前输出到控制台，
需要的是"带上下文的可读文本"。要接 JSON 日志或采集系统时，
改 `_Formatter` 一处即可，不必现在付出依赖成本。
"""

import logging
import sys
import uuid
from contextvars import ContextVar

# 请求级上下文。
#
# 用 `ContextVar` 而不是模块级变量：FastAPI 用 asyncio 并发处理
# 请求，模块级变量会让并发请求互相覆盖 request_id ——
# 那比没有 request_id 更糟（会指错方向的线索）。
_request_id: ContextVar[str | None] = ContextVar(
    "request_id", default=None
)
_user_id: ContextVar[int | None] = ContextVar(
    "user_id", default=None
)
_session_id: ContextVar[int | None] = ContextVar(
    "session_id", default=None
)


def new_request_id() -> str:
    """生成一个短的请求 ID。

    取 uuid4 前 12 位而不是完整 36 位：它只需在同时段的日志里
    不冲突，短一点便于用户口述与复制。
    """

    return uuid.uuid4().hex[:12]


def set_request_id(value: str | None) -> None:
    _request_id.set(value)


def get_request_id() -> str | None:
    return _request_id.get()


def set_user_id(value: int | None) -> None:
    _user_id.set(value)


def set_session_id(value: int | None) -> None:
    _session_id.set(value)


class _ContextFormatter(logging.Formatter):
    """把请求上下文拼进每条日志。

    格式：
    `时间 级别 [request_id user=… session=…] 模块: 消息`

    没有上下文时省掉整个方括号，避免日志里出现空的 `[]` ——
    那种噪音会让人以为是解析问题。
    """

    def format(self, record: logging.LogRecord) -> str:
        parts = []

        request_id = _request_id.get()
        if request_id:
            parts.append(request_id)

        user_id = _user_id.get()
        if user_id is not None:
            parts.append(f"user={user_id}")

        session_id = _session_id.get()
        if session_id is not None:
            parts.append(f"session={session_id}")

        # 末尾补一个空格，让模板里的 %(context)s %(name)s
        # 在 context 为空时不会产生两个空格。
        record.context = (
            " ".join(parts) + " " if parts else ""
        )
        return super().format(record)

_configured = False


def configure_logging(level: str = "INFO") -> None:
    """配置根 logger。幂等 —— 多次调用不会重复加 handler。

    为什么需要幂等：`main.py` 与验证脚本都会调它，
    重复加 handler 会让同一条日志打印多次。
    """

    global _configured

    if _configured:
        return

    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(
        _ContextFormatter(
            # `%(context)s` **不带**固定尾随空格：上下文为空时
            # 它会再加一个空格，于是多出一个空格。
            # 空格由 `_ContextFormatter` 在非空时自己补。
            #
            # 注意 `INFO` 后面本来就是**两个空格**：一个来自
            # `%(levelname)-5s` 的左对齐填充（`INFO ` 补到 5 宽），
            # 一个是字面空格。这是有意的列对齐，不是 bug。
            fmt=(
                "%(asctime)s %(levelname)-5s "
                "%(context)s%(name)s: %(message)s"
            ),
            datefmt="%H:%M:%S",
        )
    )

    root = logging.getLogger()
    root.handlers = [handler]
    root.setLevel(level.upper())

    # SQLAlchemy 的 INFO 日志会把每条 SQL 打出来。
    # 那在本项目的开发期有用（验证脚本依赖它看行为），
    # 但会让应用日志不可读 —— 因此调高到 WARNING。
    logging.getLogger("sqlalchemy.engine").setLevel(logging.WARNING)

    _configured = True


def get_logger(name: str) -> logging.Logger:
    return logging.getLogger(name)
