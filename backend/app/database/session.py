from sqlalchemy.ext.asyncio import (
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from app.core.config import settings


# SQL 日志由 `settings.sql_echo` 控制，**默认关闭**。
#
# ⚠️ 此前这里硬编码 `echo=True`：开启时 SQLAlchemy 会用自己的
# handler 再打一遍，于是每条 SQL 出现两次、应用的访问日志被淹没，
# 而且 `.env` 里关不掉 —— 属于"调试开关被写死进代码"。
# 需要看 SQL 时在 `.env` 设 `SQL_ECHO=true`。
#
# ---------------------------------------------------------------- 连接健康
#
# `pool_pre_ping=True`：**取用连接前先做一次廉价探活**，
# 若连接已死则自动丢弃并重建。
#
# 为什么必须有它：空闲连接会被服务器或中间网络设备关掉
# （Postgres 重启、Docker 网络重建、云负载均衡器的空闲超时）。
# 没有 pre-ping 时，池子里那些**看起来还活着**的死连接
# 会被取出来用，于是每次请求都失败一次。
#
# 成本：从池里取连接时多一个极轻的往返（通常是 `SELECT 1`），
# 相对于"静默失败"可以忽略。
#
# ⚠️ 但它**不是全部**：pre-ping 处理"单个连接过期"，
# 处理不了"整个池子指向的链路断了"（例如 Docker 网络被重建）。
# 后者需要 `dispose()` 重建池子 —— 见 `app/core/db_recovery.py`。
engine = create_async_engine(
    settings.database_url,
    echo=settings.sql_echo,
    pool_pre_ping=True,
    # 主动回收：把连接的存活时间限制在一小时内。
    #
    # 与 pre-ping 是**互补**关系：pre-ping 是"用之前检查"，
    # recycle 是"到点就换"。两者都做，才能覆盖
    # "服务器悄悄关了连接"与"连接活了太久"两类情况。
    pool_recycle=3600,
)


AsyncSessionLocal = async_sessionmaker(
    bind=engine,
    class_=AsyncSession,
    expire_on_commit=False,
)


async def get_db():
    async with AsyncSessionLocal() as session:
        try:
            yield session
        finally:
            await session.close()
