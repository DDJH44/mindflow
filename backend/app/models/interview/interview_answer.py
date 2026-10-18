from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, Integer, JSON, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.core.time_convention import utc_now
from app.database.base import Base


class InterviewAnswer(Base):
    __tablename__ = "interview_answers"

    id: Mapped[int] = mapped_column(
        Integer,
        primary_key=True,
        index=True,
    )

    question_id: Mapped[int] = mapped_column(
        ForeignKey(
            "interview_questions.id",
            ondelete="CASCADE",
        ),
        nullable=False,
        index=True,
    )

    answer: Mapped[str] = mapped_column(
        Text,
        nullable=False,
    )

    # 逐轮分析的留档（缺口 / 三个维度档位 / 优点与建议）。
    #
    # 为什么要存：这份分析此前**算完就丢** —— 只用来生成追问，
    # 然后随请求结束消失。后果是"面试结束后也无从复盘
    # 这一题我漏了什么"，而那正是面试练习工具的核心价值。
    #
    # 为什么不展示在作答过程中：`routers/interviews.py` 有一条
    # 刻意的决定 —— 面试中途把"技术深度偏低"给候选人看会干扰
    # 后续作答。那条决定保留，展示放到**结束后的复盘**。
    #
    # 为什么可空：既有记录没有它。历史面试在复盘时只能看到
    # 问答本身 —— 那是事实，不该伪造。
    analysis_snapshot: Mapped[dict | None] = mapped_column(
        JSON,
        nullable=True,
    )

    created_at: Mapped[datetime] = mapped_column(
        DateTime,
        default=utc_now,
        nullable=False,
    )