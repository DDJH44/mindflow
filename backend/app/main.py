import asyncio
from contextlib import asynccontextmanager

from fastapi import FastAPI

from app.core.config import settings
from app.core.logging import get_logger
from app.core.middleware import install_middleware
from app.database.base import Base
from app.database.session import engine

# 导入所有模型，确保 SQLAlchemy 注册它们
from app.models.user import User
from app.models.project import Project
from app.models.document import Document
from app.models.document_index_job import DocumentIndexJob

from app.routers.auth import router as auth_router
from app.routers.projects import router as projects_router
from app.routers.documents import router as documents_router
from app.routers.interviews import router as interviews_router
from app.routers.usage import router as usage_router


logger = get_logger("app.main")


@asynccontextmanager
async def lifespan(app: FastAPI):

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


app = FastAPI(
    title=settings.app_name,
    version="0.1.0",
    lifespan=lifespan,
)

# 配置日志并挂上请求上下文中间件。
# 放在 `include_router` 之前，保证所有路由的请求都被记录。
install_middleware(app)


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