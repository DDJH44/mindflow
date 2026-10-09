from datetime import datetime

from sqlalchemy import (
    JSON,
    DateTime,
    ForeignKey,
    Integer,
    String,
    text,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database.base import Base


class InterviewSession(Base):
    __tablename__ = "interview_sessions"

    # 所有者对象。
    #
    # 用 `lazy="selectin"` 而不是默认的惰性加载：
    # 异步 SQLAlchemy 不允许在 await 之外触发惰性加载，
    # 而面试流程需要读取用户的额度配置（生成每题时都要判）。
    # 预加载一次比每次单独查更省往返，也避免 accidental lazy load 报错。
    user: Mapped["User"] = relationship(  # noqa: F821
        lazy="selectin",
    )

    id: Mapped[int] = mapped_column(
        Integer,
        primary_key=True,
        index=True,
    )

    # 会话所有者。**冗余字段**，可由 project.owner_id 推导（§5.4 规划字段）。
    #
    # 为什么冗余：
    # 1. 所有权校验目前要 join projects 才知道会话属于谁 ——
    #    多一次 join，且校验散落在每个端点上
    # 2. 面试数据将来归 AI 服务（ADR-030），会话自身带上所有者
    #    才能独立于业务侧完成权限判断
    #
    # 不变式：新建时 `user_id` 必须等于 `project.owner_id`
    # （由 service 层强制，见 InterviewSessionService.create_session）。
    #
    # 项目转移所有权时本字段**不会**自动跟随 —— 这是有意的：
    # 历史面试记录应当归属于当时的使用者，而不是随项目易主。
    # 因此所有权校验同时检查两者（见 routers/interviews.py），
    # 而不是只信其中一个：只信 user_id 会漏掉"项目已易主"，
    # 只信 project.owner_id 会漏掉"会话创建者已无权"。
    user_id: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )

    project_id: Mapped[int] = mapped_column(
        ForeignKey("projects.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )

    # 会话状态。合法取值与转移规则见
    # app/services/interview/interview_state_machine.py（MIND_FLOW_PLAN.md §10）。
    #
    # 新建会话从 draft 开始。
    # 历史数据里的 "created" 语义等于 draft，
    # 由状态机的 LEGACY_STATUS_MAP 兼容，不需要数据回填。
    status: Mapped[str] = mapped_column(
        String(30),
        default="draft",
        nullable=False,
        index=True,
    )

    interview_type: Mapped[str] = mapped_column(
        String(30),
        default="technical",
        nullable=False,
    )

    # 面试目标岗位。§5.4 规划中该字段名为 target_role。
    target_role: Mapped[str | None] = mapped_column(
        String(100),
        nullable=True,
    )

    # 面试计划快照。
    #
    # §5.4 要求计划"可保存、可复现"（Interview Planner 的约束）。
    # 存 JSON 而不是建表：计划是**一次性的快照**，
    # 生成后可整体读出用于复盘，没有按字段查询的需求。
    #
    # 当前 Interview Planner 尚未实现（§9.2 状态 🔵），
    # 因此该列暂时为空。先建列的理由：
    # 下一次 schema 变更会同时涉及状态机与计划，
    # 与其分两次迁移，不如一次把结构留好。
    plan_snapshot: Mapped[dict | None] = mapped_column(
        JSON,
        nullable=True,
        default=dict,
    )

    current_question_index: Mapped[int] = mapped_column(
        Integer,
        default=0,
        nullable=False,
    )

    # ---------------- 题目预算与终止 ----------------
    #
    # §9.3 要求"每轮题目预算、主题覆盖与终止条件必须由
    # `InterviewSession` 持久化"，这三个字段承担该职责。
    #
    # 必须先有预算，服务端才能强制结束一场面试；
    # 在此之前面试只能靠用户主动点结束 —— 既不控成本，
    # 也无法定义"一轮面试"何时完成（见 §19 MVP 完成定义）。

    # 题目预算上限（含追问）。
    #
    # 追问也计入预算，因为它同样消耗一次 LLM 分析 + 一次生成；
    # 若不计入，一条长追问链能把成本推到远超预期。
    max_questions: Mapped[int] = mapped_column(
        Integer,
        default=8,
        nullable=False,
        server_default=text("8"),
    )

    # 已问过的题目总数（含追问）。
    #
    # 为什么不复用 `current_question_index`：
    # 后者是"下一个要分配的索引"，含义是索引分配；
    # 这里是"实际已经问了几道"，是预算计数的依据。
    # 两者绝大多数时候相等，但语义不同 —— 合并之后
    # 一旦出现索引跳过或重复，预算判断就会跟着错。
    questions_asked: Mapped[int] = mapped_column(
        Integer,
        default=0,
        nullable=False,
        server_default=text("0"),
    )

    # 结束原因（取值见 interview_termination.TerminationReason）。
    #
    # 必须记录原因而不是只记"已结束"：
    # 业务侧要区分"额度用完"与"用户自己关了"，后续动作不同；
    # 复盘时也要能回答"这场面试为什么停在第 6 题"。
    termination_reason: Mapped[str | None] = mapped_column(
        String(30),
        nullable=True,
    )

    # ---------------- 暂停 / 恢复 ----------------
    #
    # 暂停必须记住"从哪来"，否则恢复时只能一律回到 asking ——
    # 那会把处于 waiting_for_answer 的会话错误地拉回提问态。
    # 状态机据此动态计算 paused 的合法恢复目标
    # （见 interview_state_machine.allowed_transitions）。
    #
    # 恢复后清空该字段，避免残留值被误读为"当前挂起的目标"。
    resume_status: Mapped[str | None] = mapped_column(
        String(30),
        nullable=True,
    )

    # 暂停原因。用户主动暂停与系统因错误暂停需要区分，
    # 复盘时"为什么停在这里"往往是关键信息。
    pause_reason: Mapped[str | None] = mapped_column(
        String(200),
        nullable=True,
    )

    # 最近一次暂停的时间。
    #
    # 它使"暂停了多久"这类问题**可以从数据推导**，
    # 而不是再存一个时长字段 —— 存下来的时长会随时间失真
    # （这正是"可从当前状态推导的信息一律不存"的取舍，
    # 见 MIND_FLOW_PLAN.md §22）。
    paused_at: Mapped[datetime | None] = mapped_column(
        DateTime,
        nullable=True,
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