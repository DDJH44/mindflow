import asyncio
from contextlib import asynccontextmanager

# ⚠️ **必须是第一个应用内导入。**
#
# 它阻止 `pymilvus` 在导入时把 `.env` 注入 `os.environ`
# （`pymilvus/settings.py` 里有一句无参数的 `load_dotenv()`）。
#
# 为什么必须"最前面"：那个注入一旦发生就无法撤回 ——
# 而 `os.environ` 的优先级**高于** `.env`，
# 于是应用会读到被注入的旧值。
#
# 实测踩过：`backend/` 下遗留了一个五周前的 `.env`，
# 从该目录启动时 pymilvus 先注入其中的**旧 SECRET_KEY**，
# 导致一批套件报 401，而失败信息完全指向别处。
#
# 详见 `app/core/block_dotenv_pollution.py` 的模块文档。
from app.core import block_dotenv_pollution  # noqa: F401

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.core.config import settings
from app.core.logging import get_logger
from app.core.middleware import install_middleware
from app.database.base import Base
from app.database.session import engine

# 导入所有模型，确保 SQLAlchemy 注册它们
from app.models.user import User
from app.models.invite_code import InviteCode
from app.models.project import Project
from app.models.document import Document
from app.models.document_index_job import DocumentIndexJob
from app.models.knowledge_graph import (
    KnowledgeEntity,
    KnowledgeRelation,
)

from app.routers.auth import router as auth_router
from app.routers.projects import router as projects_router
from app.routers.documents import router as documents_router
from app.routers.interviews import router as interviews_router
from app.routers.usage import router as usage_router


logger = get_logger("app.main")


# 已知的占位密钥片段。
#
# 用**片段匹配**而不是精确比较：占位值会被改来改去
# （加年份、改项目名），而它们都有共同特征 ——
# 含 "change"、"placeholder"、"dev" 这类词。
_PLACEHOLDER_MARKERS = (
    "change_later",
    "changeme",
    "change_me",
    "placeholder",
    "your_secret",
    "replace_me",
    "todo",
)


def _check_secret_key() -> None:
    """校验 JWT 签名密钥。

    ## 为什么必须校验

    占位密钥最危险的地方是**它能正常工作** ——
    开发、测试、部署全都不报错，直到有人用它伪造出合法的登录令牌。
    也就是说：**错误不会在出错的那一刻显现，而是在被利用的那一刻。**

    因此启动时就要判定：

    | 环境 | 占位密钥 | 行为 |
    | --- | --- | --- |
    | development | 是 | **警告**（开发方便，不该拦）|
    | 其它 | 是 | **拒绝启动** |
    | 任何 | 长度 < 32 | 警告（熵不足）|

    "拒绝启动"是刻意的：部署时用占位密钥是**安全缺陷**，
    而不是"配置不完整" —— 它必须被看见。
    """

    key = settings.secret_key or ""
    lowered = key.lower()

    is_placeholder = any(
        marker in lowered for marker in _PLACEHOLDER_MARKERS
    )

    is_dev = settings.app_env.lower() in ("development", "dev", "local")

    if is_placeholder and not is_dev:
        raise RuntimeError(
            f"SECRET_KEY 使用了占位值，而 APP_ENV={settings.app_env!r} "
            "表明这不是开发环境。\n"
            "占位密钥能被用来伪造任意用户的登录令牌 —— "
            "这不是「配置不完整」，是安全缺陷。\n"
            "生成一个真密钥：\n"
            "    uv run python -m scripts.generate_secret\n"
            "然后写进 .env 的 SECRET_KEY="
        )

    if is_placeholder:
        logger.warning(
            "SECRET_KEY 是占位值 —— 本地开发可以，"
            "**部署前必须换成随机值**（uv run python -m "
            "scripts.generate_secret）"
        )
    elif len(key) < 32:
        logger.warning(
            "SECRET_KEY 只有 %d 字符，熵可能不足；"
            "建议至少 32 字符（uv run python -m "
            "scripts.generate_secret）",
            len(key),
        )


@asynccontextmanager
async def lifespan(app: FastAPI):

    _check_secret_key()

    # 启动时创建数据库表
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    # 启动索引 worker（§28）。
    #
    # 放在应用进程内是为了"一条命令就能用"；多副本部署时
    # 用 `WORKER_ENABLED=false` 关掉、另起独立进程。
    # `FOR UPDATE SKIP LOCKED` 保证即便忘了关也不会重复处理 ——
    # 那种情况只是浪费连接，不会出错。
    stop_event: asyncio.Event | None = None
    worker_task: asyncio.Task | None = None

    if settings.worker_enabled:
        from app.database.session import AsyncSessionLocal
        from app.services.document_index_worker import (
            DocumentIndexWorker,
        )

        stop_event = asyncio.Event()
        worker_task = asyncio.create_task(
            DocumentIndexWorker(AsyncSessionLocal).run_forever(
                poll_interval=settings.worker_poll_seconds,
                stop_event=stop_event,
            )
        )
    else:
        logger.info(
            "索引 worker 未启动（WORKER_ENABLED=false）—— "
            "确认另有独立 worker 在运行，否则索引任务不会被处理"
        )

    try:
        yield
    finally:
        # 停止 worker 并等它退出。
        #
        # 不等的话，进程退出时 worker 可能正持有数据库连接，
        # 表现为关闭期的报错噪音。
        if stop_event is not None:
            stop_event.set()

        if worker_task is not None:
            try:
                await asyncio.wait_for(worker_task, timeout=10)
            except asyncio.TimeoutError:
                logger.warning("索引 worker 未在 10 秒内退出，已放弃等待")
                worker_task.cancel()
            except Exception as exc:  # noqa: BLE001
                logger.error("索引 worker 退出时出错：%s", exc)

        # 释放限流用的 Redis 连接池（§32）。
        #
        # 不释放的话，进程退出时连接不会被正常关闭，
        # Redis 侧会留下一批"僵死"连接直到超时 ——
        # 反复重启（开发时很常见）会积累到把连接数占满。
        #
        # 单独 try：清理失败不该让关闭流程报错。
        try:
            from app.core.rate_limit import close_redis

            await close_redis()
        except Exception as exc:  # noqa: BLE001
            logger.warning("释放 Redis 连接池时出错：%s", exc)


app = FastAPI(
    title=settings.app_name,
    version="0.1.0",
    lifespan=lifespan,
)

# 配置日志并挂上请求上下文中间件。
# 放在 `include_router` 之前，保证所有路由的请求都被记录。
install_middleware(app)


# ---------------------------------------------------------------- CORS
#
# **默认不启用** —— 因为前端把 `/api` 代理到后端，
# 浏览器看到的是同源请求，不需要 CORS。
#
# 只有前后端**不同源**部署时才配置 `CORS_ALLOW_ORIGINS`。
#
# ⚠️ 这里刻意**不**提供"图省事就允许全部"的开关。
# `allow_origins=["*"]` 配合 `allow_credentials=True` 会让
# **任意网站**都能带着用户凭据调这个 API ——
# 而它在本地测试时完全看不出问题（因为本地本来就同源）。
if settings.cors_allow_origins.strip():
    _origins = [
        item.strip()
        for item in settings.cors_allow_origins.split(",")
        if item.strip()
    ]

    app.add_middleware(
        CORSMiddleware,
        allow_origins=_origins,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    logger.info("CORS 已启用，允许来源：%s", _origins)
else:
    logger.info(
        "CORS 未启用（默认）—— 前端通过同源代理访问后端。"
        "若前后端不同源部署，请配置 CORS_ALLOW_ORIGINS"
    )


@app.get("/")
async def root():

    return {
        "message": "Welcome to MindFlow AI"
    }


@app.get("/api/health")
async def health():

    return {
        "status": "ok",
        "service": settings.app_name,
        "environment": settings.app_env,
    }


app.include_router(auth_router)
app.include_router(projects_router)
app.include_router(documents_router)
app.include_router(interviews_router)
app.include_router(usage_router)