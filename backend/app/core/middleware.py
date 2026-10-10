"""HTTP 中间件：请求日志与 request_id。

只做两件事：

1. **给每个请求分配 request_id**，写进日志上下文，并通过
   响应头 `X-Request-ID` 返回。用户报错时能直接给出这个 ID，
   运维据此把散落在路由/服务/仓储的日志串起来。
2. **记录一行访问日志**：方法、路径、状态码、耗时、request_id。

**一个取舍：中间件不解析用户身份。**

用户 ID 要等认证依赖解析完 JWT 才知道，中间件阶段拿不到。
在中间件里自己解一次 JWT 会带来两个问题：
- 认证逻辑出现第二种实现，两边迟早不一致；
- 中间件要处理"未认证"等分支，而那不是它的职责。

因此改为**让认证依赖把 user_id 写进日志上下文**（见
`app/core/dependencies.py`）—— 它本来就已经解出了用户。
这样从认证完成那一刻起的日志都带上 `user=…`，
而认证之前的日志没有，这正是事实。

**同样地**，路径里的 `interview_id` 会被写进上下文的 `session`
字段：面试相关的报错几乎都要问"是哪一场"，而从路径取
比让每个服务自己写要可靠。
"""

import time

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request

from app.core.logging import (
    configure_logging,
    get_logger,
    new_request_id,
    set_request_id,
    set_session_id,
)

# 客户端可以传这个头来串联跨系统的调用（例如网关已生成）。
# 允许外部指定是有意的：分布式排查时需要沿用上游的 ID。
REQUEST_ID_HEADER = "X-Request-ID"

logger = get_logger("app.request")


class RequestContextMiddleware(BaseHTTPMiddleware):
    """绑定请求上下文并打印访问日志。"""

    async def dispatch(self, request: Request, call_next):
        # 上游已带 ID 就沿用，否则新生成。
        # 不校验格式：它只是关联标识，不是安全边界。
        request_id = request.headers.get(REQUEST_ID_HEADER) or (
            new_request_id()
        )

        set_request_id(request_id)

        # 路径参数里的会话 ID。
        #
        # `request.path_params` 在中间件阶段通常还是空的
        # （路由尚未匹配），因此从原始路径里取。
        # 只认纯数字，避免把 `/profile` 这类当成 ID。
        raw_id = request.path_params.get("interview_id")

        if raw_id is None:
            segments = [
                item
                for item in request.url.path.split("/")
                if item
            ]
            # 形如 /api/interviews/123/...
            if (
                len(segments) >= 3
                and segments[0] == "api"
                and segments[1] == "interviews"
                and segments[2].isdigit()
            ):
                raw_id = segments[2]

        if raw_id is not None:
            try:
                set_session_id(int(raw_id))
            except (TypeError, ValueError):
                set_session_id(None)

        started = time.monotonic()

        try:
            response = await call_next(request)
        except Exception:
            # 未捕获异常也要留下可关联的日志 ——
            # 否则用户拿到 500 而日志里查不到对应请求。
            elapsed = (time.monotonic() - started) * 1000
            logger.exception(
                "%s %s 未捕获异常（%.0fms）",
                request.method,
                request.url.path,
                elapsed,
            )
            raise

        elapsed = (time.monotonic() - started) * 1000

        # 慢请求用 WARNING：它能提示"用户是不是在等太久"，
        # 而本项目的 LLM 调用正常就要十几秒，因此阈值设在 30s。
        log = logger.warning if elapsed >= 30_000 else logger.info

        log(
            "%s %s → %d（%.0fms）",
            request.method,
            request.url.path,
            response.status_code,
            elapsed,
        )

        response.headers[REQUEST_ID_HEADER] = request_id

        return response


def install_middleware(app) -> None:
    """挂上中间件并配置日志。"""

    configure_logging()
    app.add_middleware(RequestContextMiddleware)
