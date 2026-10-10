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
engine = create_async_engine(
    settings.database_url,
    echo=settings.sql_echo,
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