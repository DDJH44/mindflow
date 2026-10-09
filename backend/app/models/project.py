from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.database.base import Base


class Project(Base):
    """项目模型"""

    __tablename__ = "projects"

    # 项目 ID
    id: Mapped[int] = mapped_column(
        Integer,
        primary_key=True,
        index=True,
    )

    # 项目名称
    name: Mapped[str] = mapped_column(
        String(100),
        nullable=False,
    )

    # 项目描述
    description: Mapped[str | None] = mapped_column(
        Text,
        nullable=True,
    )

    # 项目所属用户
    owner_id: Mapped[int] = mapped_column(
        ForeignKey(
            "users.id",
            ondelete="CASCADE",
        ),
        nullable=False,
        index=True,
    )

    # 项目状态
    status: Mapped[str] = mapped_column(
        String(20),
        default="active",
        nullable=False,
    )

    # 创建时间
    created_at: Mapped[datetime] = mapped_column(
        DateTime,
        default=datetime.utcnow,
        nullable=False,
    )

    # 更新时间
    updated_at: Mapped[datetime] = mapped_column(
        DateTime,
        default=datetime.utcnow,
        onupdate=datetime.utcnow,
        nullable=False,
    )