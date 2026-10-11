"""慢响应的打桩后端：用于验证"长等待提示"。

## 为什么需要它

§31.5 写明的验收方式：**第一期的验收必须包含"慢响应"场景**。

只测快路径会漏掉这个功能**唯一要解决的问题** ——
如果请求在 1 秒内返回，等待提示根本没机会显示，
测了等于没测。

## 它做什么

把 `/answer` 与 `/finish` 各延迟 60 秒再返回真实响应。
延迟发生在真实处理**之后**，因此响应内容是真的，
只是慢 —— 这正是要模拟的场景。

用法：

    uv run python -m app.core.slow_backend

然后前端仍访问原地址（后端换到 8001，前端代理指向它）：

    cd web && VITE_BACKEND_ORIGIN=http://127.0.0.1:8001 npx vite --port 5174
"""

import asyncio

import uvicorn
from starlette.middleware.base import BaseHTTPMiddleware

# 延迟秒数。
#
# 取 60 是因为实测最慢的一次是 60.8s —— 要覆盖真实的最坏情况，
# 而不是一个"看起来慢"的短延迟。
DELAY_SECONDS = 60

# 需要延迟的路径片段。
SLOW_PATHS = ("/answer", "/finish")


class SlowMiddleware(BaseHTTPMiddleware):
    """给指定路径加固定延迟。"""

    async def dispatch(self, request, call_next):
        path = request.url.path

        if any(marker in path for marker in SLOW_PATHS):
            print(
                f"  [slow] {request.method} {path} "
                f"延迟 {DELAY_SECONDS}s 后返回"
            )
            # 先等待，再交给真实处理 ——
            # 这样响应内容是真的，只是慢。
            await asyncio.sleep(DELAY_SECONDS)

        return await call_next(request)


def main() -> None:
    from app.main import app

    app.add_middleware(SlowMiddleware)

    print(f"  已启用慢响应打桩：{SLOW_PATHS} 各延迟 {DELAY_SECONDS}s")
    print("  启动端口 8001（供等待提示的走查使用）")

    uvicorn.run(app, host="127.0.0.1", port=8001, log_level="warning")


if __name__ == "__main__":
    main()
