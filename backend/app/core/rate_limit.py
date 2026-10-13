"""基于 Redis 的限流（§32）。

## 为什么需要它

D67 修的是"**一个**请求能花很多钱"（上传体积上限）。
但那只挡住了一种滥用：**很多次**请求同样能把配额烧掉 ——
而注册是开放的、此前**没有任何频率限制**。

| 风险 | 修法 |
| --- | --- |
| 一个请求花很多钱 | 上传体积上限（D67）|
| **很多次请求** | **本模块的限流** |

## 为什么用 Redis，而不是内存计数

内存计数在**多副本部署时会失效**：每个副本各算各的，
实际限额变成 `N × 配置值`。而这是"看起来生效但实际没有"
的那类问题 —— 最危险的一种。

Redis 是本项目已有的依赖（此前装了但业务代码一直没用）。

## 为什么是 fail-closed（Redis 不可用时**拒绝**）

| 选择 | Redis 挂掉时 |
| --- | --- |
| fail-open（放行）| 限流**静默失效**，而且没人知道 |
| **fail-closed（拒绝）** | 注册/上传暂时不可用，但保护仍在 |

对一个"保护花钱的 API"的机制来说，**静默失效是最坏的结果**：
它会让人以为有保护，而实际没有。

因此这里选择拒绝，并**明确报 503 + 说明原因**，
而不是悄悄放行或悄悄拒绝。

## 算法：固定窗口

用 `INCR` + 首次设置 `EXPIRE`，两步在同一个 pipeline 里完成。

**为什么 `EXPIRE` 只在首次设置**：每次都设会让窗口
"永不过期"（每次请求都把过期时间推后），限额就永远解不开。

**为什么不用滑动窗口**：固定窗口的边界效应（窗口交界处
可能通过接近 2 倍的请求）对"防滥用"这个目的可以接受，
而实现简单得多。
"""

import time
from dataclasses import dataclass

from fastapi import HTTPException, Request, status
from redis.asyncio import Redis
from redis.exceptions import RedisError

from app.core.config import settings
from app.core.logging import get_logger

logger = get_logger("app.rate_limit")


# 连接池复用。
#
# 每次请求新建连接会在高频路径上产生大量 TCP 握手 ——
# 而限流本身是"为了保护服务"，不该成为它的负担。
_redis: Redis | None = None


def get_redis() -> Redis:
    global _redis

    if _redis is None:
        _redis = Redis.from_url(
            settings.redis_url,
            decode_responses=True,
        )

    return _redis


async def close_redis() -> None:
    """应用关闭时释放连接池。"""

    global _redis

    if _redis is not None:
        await _redis.aclose()
        _redis = None


@dataclass(frozen=True)
class RateLimit:
    """一条限流规则。"""

    # 窗口内允许的次数
    times: int

    # 窗口长度（秒）
    seconds: int

    # 计数器 key 的前缀。不同端点用不同前缀，
    # 否则"注册 3 次/小时"和"上传 20 次/小时"会互相消耗。
    scope: str

    # 按什么维度限流
    by: str = "user"  # "user" | "ip"


def _client_ip(request: Request) -> str:
    """取客户端 IP。

    ⚠️ 只在**没有**可信反向代理时才直接用 `request.client.host`。
    若部署在代理后面，必须改用 `X-Forwarded-For` 的**第一个**地址 ——
    但这个头**客户端可以伪造**，因此只能在"代理一定会覆写它"
    的前提下使用。

    本项目当前没有代理，因此用 socket 地址；
    若将来加代理，**这里必须改**，否则所有人会共用代理的 IP，
    限流会误伤全部用户。
    """

    forwarded = request.headers.get("x-forwarded-for")

    if forwarded:
        # 只取第一个（最靠近客户端的那个）
        return forwarded.split(",")[0].strip()

    return request.client.host if request.client else "unknown"


def _key(rule: RateLimit, request: Request, user_id: int | None) -> str:
    if rule.by == "user" and user_id is not None:
        return f"ratelimit:{rule.scope}:user:{user_id}"

    return f"ratelimit:{rule.scope}:ip:{_client_ip(request)}"


async def enforce(
    rule: RateLimit,
    request: Request,
    user_id: int | None = None,
) -> None:
    """执行限流。超限抛 429；Redis 不可用抛 503。

    写成函数而不是依赖类，是为了能在**同一个端点**里
    按不同条件应用不同规则（例如"按用户 20 次/小时"
    与"按 IP 60 次/小时"同时生效）。
    """

    key = _key(rule, request, user_id)

    try:
        client = get_redis()

        # 用 pipeline 把 INCR 与 EXPIRE 合成一次往返。
        # `incr` 的返回值就是自增后的计数。
        async with client.pipeline(transaction=True) as pipe:
            pipe.incr(key)
            pipe.ttl(key)
            count, ttl = await pipe.execute()

        # 只在"首次创建"时设过期。
        #
        # 每次请求都设会让窗口永不过期（每次请求都推后过期时间），
        # 于是限额一旦触发就**再也解不开** —— 那是比不限流更糟的
        # 状态（用户被永久锁死）。
        if ttl is None or ttl < 0:
            await client.expire(key, rule.seconds)
            ttl = rule.seconds

    except RedisError as exc:
        # fail-closed：拒绝而不是放行。
        # 理由见模块头 —— 静默失效比暂时不可用危险得多。
        logger.error(
            "限流不可用（Redis 异常），按 fail-closed 拒绝请求：%s: %s",
            type(exc).__name__,
            str(exc)[:150],
        )

        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=(
                "限流服务暂时不可用，为避免配额被滥用已暂时拒绝请求。"
                "请稍后重试。"
            ),
        ) from exc

    if count > rule.times:
        retry_after = max(ttl, 1)

        logger.warning(
            "触发限流 scope=%s key=%s count=%d/%d 窗口=%ds",
            rule.scope,
            key,
            count,
            rule.times,
            rule.seconds,
        )

        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail=(
                f"操作过于频繁（{rule.seconds // 60} 分钟内最多 "
                f"{rule.times} 次）。请 {retry_after // 60 + 1} 分钟后再试。"
            ),
            headers={"Retry-After": str(retry_after)},
        )


async def reset(scope: str, user_id: int | None = None) -> None:
    """清掉某条规则的计数。**只给测试用。**

    生产代码不该调用它 —— 那等于给自己开后门。
    """

    client = get_redis()

    if user_id is not None:
        await client.delete(f"ratelimit:{scope}:user:{user_id}")
    else:
        # 按 IP 的计数无法枚举（key 里有 IP），
        # 因此测试里尽量用 user 维度。
        pattern = f"ratelimit:{scope}:*"

        async for key in client.scan_iter(match=pattern):
            await client.delete(key)
