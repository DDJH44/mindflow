from datetime import datetime

from sqlalchemy import (
    DateTime,
    ForeignKey,
    Integer,
    String,
    UniqueConstraint,
    text,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.database.base import Base


class InterviewUsage(Base):
    """面试用量计数（按用户 / 周期 / 指标）。

    为什么需要它而不是每次现场 `COUNT(*)` 面试会话：
    额度校验发生在**创建会话之前**，而这个动作本身很频繁；
    每次聚合计数会在会话表上产生额外扫描。计数器是 O(1) 的。

    这是**派生数据**：数值可由 `interview_sessions` 重建
    （按 `user_id` + 创建时间聚合）。因此它允许丢失后重建，
    这也是为什么它归 AI 服务而不是业务服务 ——
    `interview_sessions` 由 FastAPI 拥有（ADR-030），
    计数的事实来源在同一侧，跨服务同步不必要。

    唯一约束 (user_id, period, metric) 保证"加了又加"的语义：
    同一周期同一指标只占一行，靠 `count` 累加。
    """

    __tablename__ = "interview_usage"

    __table_args__ = (
        UniqueConstraint(
            "user_id",
            "period",
            "metric",
            name="uq_interview_usage_scope",
        ),
    )

    id: Mapped[int] = mapped_column(
        Integer,
        primary_key=True,
        index=True,
    )

    user_id: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )

    # 计数周期，格式 "YYYY-MM"（按月）。
    #
    # 存字符串而不是存日期区间：
    # 周期是**账期标识**，不是时间点。字符串让唯一约束直接生效，
    # 也避免"这个月"在时区上产生歧义 —— 账期由写入时决定，
    # 不由读取时重新计算（否则跨月瞬间会读到两个不同的账期）。
    period: Mapped[str] = mapped_column(
        String(7),
        nullable=False,
    )

    # 指标名，取值见 app/services/usage/usage_quota.py 的 UsageMetric。
    metric: Mapped[str] = mapped_column(
        String(40),
        nullable=False,
    )

    count: Mapped[int] = mapped_column(
        Integer,
        default=0,
        nullable=False,
        server_default=text("0"),
    )

    created_at: Mapped[datetime] = mapped_column(
        DateTime,
        default=datetime.utcnow,
        nullable=False,
    )

    updated_at: Mapped[datetime] = mapped_column(
        DateTime,
        default=datetime.utcnow,
        onupdate=datetime.utcnow,
        nullable=False,
    )
