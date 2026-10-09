from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.database.base import Base


class Document(Base):
    """知识库文档模型"""

    __tablename__ = "documents"

    # 文档 ID
    id: Mapped[int] = mapped_column(
        Integer,
        primary_key=True,
        index=True,
    )

    # 文档名称
    name: Mapped[str] = mapped_column(
        String(255),
        nullable=False,
    )

    # 原始文件名
    original_filename: Mapped[str] = mapped_column(
        String(255),
        nullable=False,
    )

    # 文件类型
    file_type: Mapped[str] = mapped_column(
        String(50),
        nullable=False,
    )

    # 文档语义类型：resume / jd / project / code / other
    document_type: Mapped[str] = mapped_column(
        String(20),
        default="other",
        nullable=False,
        index=True,
    )

    # 文件存储路径
    file_path: Mapped[str | None] = mapped_column(
        String(500),
        nullable=True,
    )

    # 文档所属项目
    project_id: Mapped[int] = mapped_column(
        ForeignKey(
            "projects.id",
            ondelete="CASCADE",
        ),
        nullable=False,
        index=True,
    )

    # 文档解析状态
    status: Mapped[str] = mapped_column(
        String(20),
        default="pending",
        nullable=False,
    )

    # 文档原始文本
    content: Mapped[str | None] = mapped_column(
        Text,
        nullable=True,
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