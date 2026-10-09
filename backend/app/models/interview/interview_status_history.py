from datetime import datetime

from sqlalchemy import (
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    text,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.database.base import Base


class InterviewStatusHistory(Base):
    """会话状态转移轨迹。

    为什么需要它：
    会话表只保存**当前**状态。一旦状态变了，就没有任何记录能回答
    "这场面试什么时候从 asking 变成 evaluating"、"为什么停在这里"。
    这让两个本该能查的问题变成不可查：

    1. `paused` 会话的审计 —— 恢复目标存在 `resume_status` 里，
       但"什么时候暂停的、暂停前在做什么、恢复了几次"全部丢失
    2. 出错排查 —— 全会话停在某个中间状态时，无法判断是
       正常流程还是某次失败留下的残迹

    这也是 §23.4 拆服务前需要的能力：跨服务后出问题，
    本服务内的调用栈不再完整，轨迹是唯一证据。

    设计取舍 —— **记录每一次转移，包括中间态**：
    服务层会用 `commit=False` 走中间态（例如答题时
    `asking → waiting_for_answer → evaluating` 是一次事务的两步）。
    这些中间态照样记录，因为"当时到底经过了哪一步"
    正是排查时要回答的问题。轨迹因此是完整的，
    例如一轮答题会留下：

        asking             → waiting_for_answer  [answer_submitted]
        waiting_for_answer → evaluating          [analysis_completed]
        evaluating         → asking              [follow_up_generated]

    这样 `asking` 到 `asking` 之间发生了什么完全可读，
    而不是折叠成一条无法解释的"asking → asking"。

    ⚠️ **必须与状态变更在同一次事务里提交**。
    若历史先独立提交而状态随后失败，轨迹里就会出现
    一次"从未发生"的转移 —— 比没有轨迹更糟，
    因为它会把人引向错误的方向。
    因此写入侧只 add 不 commit，失败时随事务一起回滚。
    """

    __tablename__ = "interview_status_history"

    __table_args__ = (
        # 禁止自转移：状态机本就拒绝它，
        # 在库层面也拦一道，防止将来有人绕过服务层直接写表。
        CheckConstraint(
            "from_status <> to_status",
            name="ck_status_history_no_self_transition",
        ),
        # 按会话按时间回放轨迹，是最主要的查询方式。
        Index(
            "ix_status_history_session_created",
            "session_id",
            "created_at",
        ),
    )

    id: Mapped[int] = mapped_column(
        Integer,
        primary_key=True,
    )

    session_id: Mapped[int] = mapped_column(
        ForeignKey("interview_sessions.id", ondelete="CASCADE"),
        nullable=False,
    )

    # 转移前的状态。
    #
    # 保留旧值字符串（含历史上的 "created"）而不是规范化后的枚举：
    # 轨迹是**审计记录**，应当原样反映当时库里是什么值。
    # 规范化留给读取方（normalize_status 已兼容旧值）。
    from_status: Mapped[str] = mapped_column(
        String(30),
        nullable=False,
    )

    to_status: Mapped[str] = mapped_column(
        String(30),
        nullable=False,
    )

    # 触发原因，取值见 interview_state_machine.TransitionTrigger。
    #
    # 用 String 而不是数据库枚举：
    # 触发类型会随业务增加，数据库枚举每次都要写迁移。
    # 合法性由应用层的枚举保证。
    trigger: Mapped[str] = mapped_column(
        String(40),
        nullable=False,
        server_default=text("'unspecified'"),
    )

    created_at: Mapped[datetime] = mapped_column(
        DateTime,
        default=datetime.utcnow,
        nullable=False,
        server_default=text("NOW()"),
    )
