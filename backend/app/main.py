from contextlib import asynccontextmanager

from fastapi import FastAPI

from app.core.config import settings
from app.database.base import Base
from app.database.session import engine

# 导入所有模型，确保 SQLAlchemy 注册它们
from app.models.user import User
from app.models.project import Project
from app.models.document import Document

from app.routers.auth import router as auth_router
from app.routers.projects import router as projects_router
from app.routers.documents import router as documents_router
from app.routers.interviews import router as interviews_router
from app.routers.usage import router as usage_router


@asynccontextmanager
async def lifespan(app: FastAPI):

    # 启动时创建数据库表
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    yield


app = FastAPI(
    title=settings.app_name,
    version="0.1.0",
    lifespan=lifespan,
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