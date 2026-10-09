from datetime import datetime

from sqlalchemy import (
    JSON,
    Boolean,
    DateTime,
    ForeignKey,
    Integer,
    String,
    Text,
    text,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.database.base import Base


class InterviewQuestion(Base):
    __tablename__ = "interview_questions"

    id: Mapped[int] = mapped_column(
        Integer,
        primary_key=True,
        index=True,
    )

    session_id: Mapped[int] = mapped_column(
        ForeignKey(
            "interview_sessions.id",
            ondelete="CASCADE",
        ),
        nullable=False,
        index=True,
    )

    question: Mapped[str] = mapped_column(
        Text,
        nullable=False,
    )

    question_type: Mapped[str] = mapped_column(
        String(30),
        nullable=False,
    )

    question_index: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
    )

    context: Mapped[str | None] = mapped_column(
        Text,
        nullable=True,
    )

    # ---------------- 资料依据 ----------------
    #
    # 生成该问题时实际喂给模型的 DocumentChunk ID 列表。
    #
    # 为什么必须持久化（MIND_FLOW_PLAN.md §9.3 提问原则）：
    # "资料型问题必须包含可追溯 evidence_chunk_ids"，
    # 而"可追溯"是产品北极星第一条（见 §1 项目定位）。
    # 只存问题文本，事后无法回答"这道题凭什么这么问"。
    #
    # 语义约定：
    # - 非空列表 = 资料驱动的问题，列表即依据
    # - 空列表   = 通用能力问题，**不是**"资料缺失"
    #   两者必须靠 is_general 区分，不能靠是否为空
    #
    # 顺序与喂给模型的上下文一致（见 question_generator 的提取逻辑），
    # 便于人工核对与复现。
    evidence_chunk_ids: Mapped[list | None] = mapped_column(
        JSON,
        nullable=True,
        default=list,
    )

    # 是否为通用能力题（不依赖候选人资料）。
    #
    # 与 evidence_chunk_ids 配合表达两种情况：
    #   资料题：is_general=False 且 evidence_chunk_ids 非空
    #   通用题：is_general=True  且 evidence_chunk_ids 为空
    # §9.3 要求"通用能力题要显式标记为 general，不伪装成资料事实"，
    # 因此用一个显式布尔列，而不是靠列表是否为空去猜。
    is_general: Mapped[bool] = mapped_column(
        Boolean,
        nullable=False,
        default=False,
        server_default=text("false"),
    )

    created_at: Mapped[datetime] = mapped_column(
        DateTime,
        default=datetime.utcnow,
        nullable=False,
    )
