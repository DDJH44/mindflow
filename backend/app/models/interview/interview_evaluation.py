from datetime import datetime

from sqlalchemy import JSON, DateTime, ForeignKey, Integer, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.database.base import Base


class InterviewEvaluation(Base):
    __tablename__ = "interview_evaluations"

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

    overall_score: Mapped[int | None] = mapped_column(
        Integer,
        nullable=True,
    )

    technical_score: Mapped[int | None] = mapped_column(
        Integer,
        nullable=True,
    )

    project_score: Mapped[int | None] = mapped_column(
        Integer,
        nullable=True,
    )

    communication_score: Mapped[int | None] = mapped_column(
        Integer,
        nullable=True,
    )

    feedback: Mapped[str | None] = mapped_column(
        Text,
        nullable=True,
    )

    # ---------------- 结构化评价内容 ----------------
    #
    # 这三个字段此前只存在于 LLM 返回值里，没有落库
    # （见 MIND_FLOW_PLAN.md §8A.9 遗留项），
    # 而它们是 Phase 6「能力画像与训练建议」的前置数据。
    #
    # 用 JSON 数组而不是多行子表：
    # 这些内容是评价的**附属描述**，没有独立查询需求，
    # 单行 JSON 足够，也避免为三个字符串列表各建一张表。
    strengths: Mapped[list | None] = mapped_column(
        JSON,
        nullable=True,
        default=list,
    )

    weaknesses: Mapped[list | None] = mapped_column(
        JSON,
        nullable=True,
        default=list,
    )

    suggestions: Mapped[list | None] = mapped_column(
        JSON,
        nullable=True,
        default=list,
    )

    # ---------------- 评分可审计明细 ----------------
    #
    # 记录锚点选择、每次采样的分数、采样次数与判定依据。
    #
    # 为什么必须存：
    # ADR-021 承认线上与离线使用不同采样数，因此**线上分数波动更大**。
    # 若历史评分只留一个数字，出现"同一场面试两次分数不同"时
    # 就无法判断是跳档、是模型变更，还是口径不同。
    # 存下 anchor 与 sampled_scores 后，这类问题可以事后复现。
    #
    # 这里冗余存了与四个 score 列相同的值（采样明细），
    # 换来的是"能从库内独立解释一个分数是怎么来的"。
    scoring_details: Mapped[dict | None] = mapped_column(
        JSON,
        nullable=True,
        default=dict,
    )

    created_at: Mapped[datetime] = mapped_column(
        DateTime,
        default=datetime.utcnow,
        nullable=False,
    )
