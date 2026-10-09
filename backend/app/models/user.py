from datetime import datetime

from sqlalchemy import Integer, String, text
from sqlalchemy.orm import Mapped, mapped_column

from app.database.base import Base


class User(Base):

    __tablename__ = "users"

    id: Mapped[int] = mapped_column(
        primary_key=True
    )

    username: Mapped[str] = mapped_column(
        String(50),
        unique=True,
        index=True,
        nullable=False,
    )

    email: Mapped[str] = mapped_column(
        String(100),
        unique=True,
        index=True,
        nullable=False,
    )

    password_hash: Mapped[str] = mapped_column(
        String(255),
        nullable=False,
    )

    # 每月可创建的面试场次上限。
    #
    # 放在 `users` 上而不是单独的 plan/quota 表：
    # 当前只有"每用户一个数字"这一种需求，单独建表是为不存在的
    # 复杂度付费。等出现套餐、有效期、按指标分别限额时再拆
    # （§23.4 第 2 步：先让业务能力产生真实复杂度）。
    #
    # 这个字段是**业务配置**，将来归 Spring Boot；但它的校验对象
    # 是面试会话，而面试数据归 FastAPI（ADR-030）——
    # 因此在拆分之前，计数与校验都留在 FastAPI 侧。
    interview_quota: Mapped[int] = mapped_column(
        Integer,
        default=10,
        nullable=False,
        server_default=text("10"),
    )

    # 每月可生成的**题目**总数（含追问，跨所有场次）。
    #
    # 为什么还需要这一个：`InterviewSession.max_questions` 是**单场**
    # 上限，它描述的是"这一场想聊多深"，而不是成本额度。
    # 若只有单场上限，用户可以每场都设成 30 题 —— 于是那个上限
    # 反而变成了"允许 30 题"的授权，成本失控。
    #
    # 两者关系：单场上限管"一场多长"，月度量管"一共多少"。
    # 有效额度取两者中更紧的那个（见 usage_quota）。
    #
    # 取 100 的理由：默认 10 场 × 默认 8 题 = 80，留约 25% 余量，
    # 让"少开几场但每场深聊"的用法不被误伤。
    monthly_question_quota: Mapped[int] = mapped_column(
        Integer,
        default=100,
        nullable=False,
        server_default=text("100"),
    )

    created_at: Mapped[datetime] = mapped_column(
        default=datetime.utcnow
    )