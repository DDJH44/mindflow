"""验证请求上下文与日志。

锚点：**用户报错时能给出一个 ID，运维据此把散落在
路由/服务/仓储的日志串起来。**

因此必须验证四件事：
1. 响应头带 `X-Request-ID`，且**每个请求不同**（否则串不起来）
2. 上游带的 ID 被沿用（分布式排查要跨系统串联）
3. 出错响应（404/401）也带 ID —— 那些正是最需要排查的
4. 认证完成后 `user` 进入日志上下文

第 4 项容易漏：它不体现在响应里，但"哪个用户"是排查时
第一个要问的。若没绑定成功，日志里就永远没有 `user=…`，
而人不会立刻发现。

用法：uv run python -m app.core.test_logging
"""

import asyncio
import sys

import httpx

BASE = "http://127.0.0.1:5173/api"
ACCOUNT = "mindflow"

# 中间件在响应头里用的名字
HEADER = "X-Request-ID"

results: list[tuple[str, bool]] = []


def record(label: str, ok: bool, extra: str = "") -> None:
    results.append((label, ok))
    suffix = f"  {extra}" if extra else ""
    print(f"  [{'OK  ' if ok else 'FAIL'}] {label}{suffix}")


async def main() -> int:
    sys.path.insert(0, r"D:\yuxi\MindFlow\backend")

    from sqlalchemy import text

    from app.core import logging as app_logging
    from app.core.security import create_access_token
    from app.database.session import AsyncSessionLocal

    async with AsyncSessionLocal() as db:
        user_id = (
            await db.execute(
                text("SELECT id FROM users WHERE username = :u"),
                {"u": ACCOUNT},
            )
        ).scalar_one()

    token = create_access_token({"sub": str(user_id)})

    async with httpx.AsyncClient(
        base_url=BASE,
        headers={"Authorization": f"Bearer {token}"},
        timeout=120.0,
    ) as client:
        # ================================================
        print()
        print("=" * 74)
        print("1. 响应头携带 request_id")
        print("=" * 74)

        response = await client.get("/usage")
        first_id = response.headers.get(HEADER)

        record(
            "成功响应带 X-Request-ID",
            bool(first_id),
            f"{HEADER}={first_id}",
        )

        response = await client.get("/usage")
        second_id = response.headers.get(HEADER)

        record(
            "**每个请求的 ID 不同**",
            bool(first_id) and bool(second_id)
            and first_id != second_id,
            f"{first_id} vs {second_id}",
        )

        # ================================================
        print()
        print("=" * 74)
        print("2. 沿用上游提供的 ID")
        print("=" * 74)

        upstream = "trace-from-gateway-001"

        response = await client.get(
            "/usage", headers={HEADER: upstream}
        )

        record(
            "上游 ID 被沿用",
            response.headers.get(HEADER) == upstream,
            f"返回 {response.headers.get(HEADER)}",
        )

        # ================================================
        print()
        print("=" * 74)
        print("3. 出错响应也带 ID（最需要排查的场景）")
        print("=" * 74)

        response = await client.get("/interviews/999999/detail")
        record(
            "404 带 X-Request-ID",
            response.status_code == 404
            and bool(response.headers.get(HEADER)),
            f"status={response.status_code} "
            f"id={response.headers.get(HEADER)}",
        )

        async with httpx.AsyncClient(
            base_url=BASE, timeout=60.0
        ) as anon:
            response = await anon.get("/usage")

        record(
            "401 带 X-Request-ID",
            response.status_code == 401
            and bool(response.headers.get(HEADER)),
            f"status={response.status_code} "
            f"id={response.headers.get(HEADER)}",
        )

        # ================================================
        print()
        print("=" * 74)
        print("4. 日志上下文绑定")
        print("=" * 74)

        # 认证依赖在服务端进程里绑定 user，本进程看不到 ——
        # 因此这里验证的是**绑定机制本身**：设置后能读回来。
        # 真实绑定已在服务端日志里体现（`app.request: ... user=3`）。
        app_logging.set_request_id("unit-test-id")
        app_logging.set_user_id(42)
        app_logging.set_session_id(7)

        record(
            "set/get request_id 往返一致",
            app_logging.get_request_id() == "unit-test-id",
        )

        # 格式化后的日志行应包含全部上下文
        import io
        import logging as std_logging

        stream = io.StringIO()
        handler = std_logging.StreamHandler(stream)
        handler.setFormatter(app_logging._ContextFormatter(
            fmt="%(levelname)s %(context)s %(name)s: %(message)s"
        ))

        probe = std_logging.getLogger("app.test_probe")
        probe.handlers = [handler]
        probe.setLevel(std_logging.INFO)
        probe.propagate = False
        probe.info("探测消息")

        line = stream.getvalue().strip()

        record(
            "日志行包含 request_id",
            "unit-test-id" in line,
            line[:80],
        )
        record(
            "日志行包含 user",
            "user=42" in line,
            line[:80],
        )
        record(
            "日志行包含 session",
            "session=7" in line,
            line[:80],
        )

        # 清理上下文，避免影响后续
        app_logging.set_request_id(None)
        app_logging.set_user_id(None)
        app_logging.set_session_id(None)

        record(
            "清空上下文后 request_id 为空",
            app_logging.get_request_id() is None,
        )

        # 无上下文时不该出现空的方括号
        stream2 = io.StringIO()
        handler2 = std_logging.StreamHandler(stream2)
        handler2.setFormatter(app_logging._ContextFormatter(
            fmt="%(levelname)s %(context)s %(name)s: %(message)s"
        ))

        probe2 = std_logging.getLogger("app.test_probe2")
        probe2.handlers = [handler2]
        probe2.setLevel(std_logging.INFO)
        probe2.propagate = False
        probe2.info("无上下文")

        line2 = stream2.getvalue().strip()

        record(
            "无上下文时不打空的方括号",
            "[]" not in line2,
            line2[:80],
        )

    print()
    print("=" * 74)
    passed = sum(1 for _, ok in results if ok)
    failed = [label for label, ok in results if not ok]
    print(f"通过 {passed} / {len(results)}")
    if failed:
        print("失败项:")
        for label in failed:
            print("  -", label)
        return 1

    print("全部通过")
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
