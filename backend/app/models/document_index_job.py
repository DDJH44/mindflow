from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, Integer, String, Text, func
from sqlalchemy.orm import Mapped, mapped_column

from app.database.base import Base


class DocumentIndexJob(Base):
    """异步文档索引任务（MIND_FLOW_PLAN.md §28）。

    上传时入队、由 worker 领取执行，把"嵌入"这一步从请求路径里
    移出去。此前大文件同步嵌入要阻塞 37 秒以上。

    `document_id` 唯一：一个文档同时只该有一个任务。
    重复入队会把同一份文档嵌入两次 —— 白花钱，
    而且**表面上完全正常**，不会报错。

    `lease_expires_at` 是崩溃恢复的关键：worker 领走任务后崩溃，
    任务会永远停在 `running`。没有租约超时，那种故障表现为
    "界面一直转圈、日志里没有报错"，极难定位。
    """

    __tablename__ = "document_index_jobs"

    id: Mapped[int] = mapped_column(
        Integer,
        primary_key=True,
        autoincrement=True,
    )

    document_id: Mapped[int] = mapped_column(
        Integer,
        ForeignKey("documents.id", ondelete="CASCADE"),
        nullable=False,
        unique=True,
    )

    # pending / running / done / failed
    #
    # `done` 与 `failed` 都是终态。保留失败记录而不删除，
    # 是为了让"哪个文档失败了、失败几次、什么原因"可查 ——
    # 删掉记录等于让失败不可见。
    status: Mapped[str] = mapped_column(
        String(20),
        nullable=False,
        default="pending",
        server_default="pending",
    )

    attempts: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        default=0,
        server_default="0",
    )

    last_error: Mapped[str | None] = mapped_column(
        Text,
        nullable=True,
    )

    # 租约到期时间。worker 领取时写入 `now() + lease_seconds`。
    # 后台清理会把超时未完成的任务重新放回 pending。
    lease_expires_at: Mapped[datetime | None] = mapped_column(
        DateTime,
        nullable=True,
    )

    created_at: Mapped[datetime] = mapped_column(
        DateTime,
        nullable=False,
        server_default=func.now(),
    )

    updated_at: Mapped[datetime] = mapped_column(
        DateTime,
        nullable=False,
        server_default=func.now(),
        onupdate=func.now(),
    )
