from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.database.base import Base


class Resume(Base):
    """简历模型"""

    __tablename__ = "resumes"

    # 简历 ID
    id: Mapped[int] = mapped_column(
        Integer,
        primary_key=True,
        index=True,
    )

    # 所属项目
    project_id: Mapped[int] = mapped_column(
        ForeignKey(
            "projects.id",
            ondelete="CASCADE",
        ),
        nullable=False,
        index=True,
    )

    # 原始文件名
    original_filename: Mapped[str] = mapped_column(
        String(255),
        nullable=False,
    )

    # 文件存储路径
    file_path: Mapped[str] = mapped_column(
        String(500),
        nullable=False,
    )

    # 文件类型
    file_type: Mapped[str] = mapped_column(
        String(50),
        nullable=False,
    )

    # 文件大小（字节）
    file_size: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
    )

    # 提取后的简历文本
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