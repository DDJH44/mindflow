"""验证限流（§32）。

## 它防的是什么

D67 的体积上限挡的是"**一个**请求花很多钱"，
而限流挡的是"**很多次**请求"：

| 风险 | 修法 |
| --- | --- |
| 一个请求很大 | 上传体积上限（D67）|
| **很多次请求** | **限流** |
| 无限注册（反复触发昂贵操作）| 注册按 IP 限流 |
| 暴力破解口令 | 登录按 IP 限流 |

## 它验证四件事

1. **超过阈值被拒（429）** —— 限流真的生效
2. **阈值之内放行** —— 防止"把限额设成 0"这种假通过
3. **Redis 不可用时拒绝（fail-closed）**
   —— 而不是**静默放行**。这是设计选择，必须被断言守住：
   静默失效会让人以为有保护，而实际没有。
4. **计数器的过期时间真的被设置** ——
   最隐蔽的一种错误：若每次请求都刷新 TTL，
   窗口就**永不过期**，限额一旦触发就**再也解不开**。
   那比不限流更糟（用户被永久锁死）。

用法：uv run python -m tests.test_rate_limit
"""

import asyncio
import sys

import httpx
from redis.asyncio import Redis
from sqlalchemy import text

from app.database.session import AsyncSessionLocal

BASE = "http://127.0.0.1:5173/api"


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

    from app.core import rate_limit
    from app.core.config import settings

    result = Result()

    print("=" * 78)
    print("限流验证")
    print("=" * 78)

    async with AsyncSessionLocal() as db:
        user_id = (
            await db.execute(
                text("SELECT id FROM users WHERE username = :u"),
                {"u": "mindflow"},
            )
        ).scalar_one()

        project_id = (
            await db.execute(
                text(
                    "SELECT id FROM projects WHERE owner_id = :u "
                    "ORDER BY id LIMIT 1"
                ),
                {"u": user_id},
            )
        ).scalar_one()

    # ================================================
    print()
    print("=" * 78)
    print("① 计数器的过期时间必须被设置（防止窗口永不过期）")
    print("=" * 78)

    # 用一条**独立的 scope**，避免影响生产计数器
    probe_rule = rate_limit.RateLimit(
        times=3, seconds=60, scope="test_probe", by="user"
    )

    client = rate_limit.get_redis()
    await client.delete(f"ratelimit:test_probe:user:{user_id}")

    for _ in range(2):
        await rate_limit.enforce(probe_rule, _FakeRequest(), user_id)

    key = f"ratelimit:test_probe:user:{user_id}"
    ttl = await client.ttl(key)

    result.check(
        "计数器设置了过期时间",
        ttl is not None and ttl > 0,
        f"TTL={ttl} 秒",
    )

    # 关键：TTL 不应随每次请求被刷新。
    #
    # 先等到 TTL 变小，再发一次请求，看它是否被重置回 60。
    await asyncio.sleep(2)
    ttl_before = await client.ttl(key)

    await rate_limit.enforce(probe_rule, _FakeRequest(), user_id)

    ttl_after = await client.ttl(key)

    result.check(
        "后续请求**不刷新** TTL（否则窗口永不过期）",
        ttl_after <= ttl_before,
        f"{ttl_before} 秒 → {ttl_after} 秒",
    )

    await client.delete(key)

    # ================================================
    print()
    print("=" * 78)
    print("② 阈值内放行、超阈值拒绝（走真实 HTTP）")
    print("=" * 78)

    # ⚠️ **不能靠改 `settings` 来测。**
    #
    # 第一版我在本进程里把 `rate_limit_register_times` 改成 2，
    # 然后期待第 3 次被拒 —— 结果 4 次全部通过。
    #
    # 原因：**服务端是另一个进程**，它读的是它自己的
    # `settings`。改本进程的配置只影响本进程。
    #
    # 因此改为**耗尽真实限额**：连发 `times + 2` 次请求，
    # 前 `times` 次不该是 429，之后必须是 429。
    # 这样测的是**服务端实际的配置**，而不是我以为的配置。
    times = settings.rate_limit_register_times

    # 先把注册计数清零，让本套件**可重复运行**。
    #
    # 为什么必须做：`test_e2e_http` 也会注册账号（1 次），
    # 而注册限额是 5 次/小时。若不清理，两个套件会互相消耗，
    # 于是"第二次运行本套件"必然失败 —— 那是**套件之间的
    # 隐藏依赖**，而它表现为"限流坏了"。
    #
    # 按 scope 前缀清（而不是拼出某个 IP 的 key）：
    # key 里的 IP 是**服务端看到的客户端地址**，
    # 从测试进程猜它是不牢靠的（经代理时更会不同）。
    cleared = 0
    async for key in client.scan_iter(match="ratelimit:register:*"):
        await client.delete(key)
        cleared += 1

    if cleared:
        print(f"      已清零 register 计数 {cleared} 条（保证可重复运行）")

    async with httpx.AsyncClient(base_url=BASE, timeout=60.0) as http:
        statuses = []

        # 用不存在的用户名，避免真的创建账号。
        # 限流发生在**用户名检查之前**，因此这些请求不会因
        # "用户名已存在"而改变状态码。
        for index in range(times + 2):
            response = await http.post(
                "/auth/register",
                json={
                    "username": f"ratelimit_probe_{index}",
                    "email": f"ratelimit_probe_{index}@example.com",
                    "password": "probe_password_123",
                },
            )
            statuses.append(response.status_code)

        within = statuses[:times]
        beyond = statuses[times:]

        result.check(
            f"前 {times} 次未被限流",
            all(code != 429 for code in within),
            f"{within}",
        )

        result.check(
            f"第 {times + 1} 次起被拒（429）",
            all(code == 429 for code in beyond),
            f"{beyond}",
        )

    # 收尾：清掉本套件留下的注册计数，**避免影响其它套件**。
    #
    # 注册限额是 5 次/小时，若本套件耗尽后不清理，
    # 紧跟其后的 `test_e2e_http` 会因为"注册被限流"而失败 ——
    # 而那个失败看起来像 e2e 坏了。
    async for key in client.scan_iter(match="ratelimit:register:*"):
        await client.delete(key)

    # 顺手清掉可能被真的创建出来的探针账号
    async with AsyncSessionLocal() as db:
        deleted = (
            await db.execute(
                text(
                    "DELETE FROM users WHERE username LIKE "
                    "'ratelimit_probe_%' RETURNING id"
                )
            )
        ).all()
        await db.commit()

    if deleted:
        print(f"      已清理探针账号 {[r[0] for r in deleted]}")

    # ================================================
    print()
    print("=" * 78)
    print("③ Redis 不可用时必须**拒绝**（fail-closed）")
    print("=" * 78)

    # 把客户端换成指向一个必然连不上的端口。
    #
    # 这是一个**设计选择**的断言：若实现改成 fail-open，
    # 这条会失败 —— 那正是我们想要的（防止有人"顺手"改成放行）。
    real_client = rate_limit._redis

    try:
        rate_limit._redis = Redis.from_url(
            "redis://127.0.0.1:1/0",
            decode_responses=True,
            socket_connect_timeout=1,
        )

        raised = None

        try:
            await rate_limit.enforce(
                rate_limit.RateLimit(
                    times=10, seconds=60, scope="test_down", by="user"
                ),
                _FakeRequest(),
                user_id,
            )
        except Exception as exc:  # noqa: BLE001
            raised = exc

        status_code = getattr(raised, "status_code", None)

        result.check(
            "Redis 不可用时抛 503（而不是静默放行）",
            status_code == 503,
            f"异常 {type(raised).__name__}，status={status_code}",
        )

    finally:
        if rate_limit._redis is not None:
            await rate_limit._redis.aclose()

        rate_limit._redis = real_client

    # ================================================
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


class _FakeRequest:
    """最小的 Request 替身。

    限流只用到 `headers` 与 `client.host`，因此不必构造
    完整的 Starlette Request 对象。
    """

    class _Client:
        host = "127.0.0.1"

    def __init__(self) -> None:
        self.headers: dict[str, str] = {}
        self.client = self._Client()


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
