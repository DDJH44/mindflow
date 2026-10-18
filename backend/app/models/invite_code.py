"""邀请码。

## 为什么需要它

公开部署时最大的风险**不是技术问题，是钱**：

- 注册是开放的
- 每个新账号自带「10 场面试 + 100 题」额度
- 每道题约 2–4 次 LLM 调用 + 一次嵌入调用
- 这些全部记在**部署者的 key** 上

也就是说：**注册的越多人，你付的越多**，而且
没有任何机制设上限 —— 一天注册 120 个账号就是
1200 场面试的钱。

限流（`rate_limit`）解决的是"**单个人用多少**"，
不是"**多少人可以用**"。这是两件事。

## 设计：一次性邀请码

一条码只能用一次，用掉就作废。因此：

    最多账号数 = 发出去的码数

成本因此**可预先计算**：发 10 条码 = 最多 10 个账号 = 最多少 100 场。

这比"一个共享口令"严格得多 —— 后者一旦泄露（截图、转发），
限制就没了，而你无法收回。

## 为什么不是"管理员手动开账号"

那也可行，但每条账号都要人操作一次，而且密码要经人转达 ——
转达渠道本身又是一个泄露点。发码让用户自己设置密码，
更安全也省事。

## `used_by_user_id` 为 NULL 表示未使用

这是唯一的状态判据，不另设 `is_used` 布尔列 ——
两个字段表达同一件事，迟早会不一致（D63/D70 的教训：
**同一个事实只应存在一处**）。

## `expires_at` 可空

留空表示永不过期。但对**体验用的邀请码**建议设过期时间：
一条两年前发出去、忘了收回的码，是长期的后门。
"""

from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.core.time_convention import utc_now
from app.database.base import Base


class InviteCode(Base):
    """一条邀请码。"""

    __tablename__ = "invite_codes"

    id: Mapped[int] = mapped_column(
        Integer,
        primary_key=True,
        index=True,
    )

    # 码本身。**必须是随机串，不能是可猜的序号** ——
    # 可猜的码等于没有邀请制。
    code: Mapped[str] = mapped_column(
        String(64),
        nullable=False,
        unique=True,
        index=True,
    )

    # 备注：写给谁的、为什么发。
    #
    # 为什么需要它：码用掉之后，`used_by_user_id` 只能告诉你
    # "被谁用了"，而记不住"本来打算给谁"。
    # 半个月后回来清理时，那正是需要的信息。
    note: Mapped[str | None] = mapped_column(
        String(200),
        nullable=True,
    )

    # 使用者。**NULL = 未使用**（见模块文档）。
    # `ondelete="SET NULL"`：删掉用户不该连带删掉邀请记录 ——
    # 那会让"已经用掉的码"变回"可用"，等于凭空多出一个后门。
    used_by_user_id: Mapped[int | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )

    used_at: Mapped[datetime | None] = mapped_column(
        DateTime,
        nullable=True,
    )

    # 过期时间。NULL = 永不过期。
    expires_at: Mapped[datetime | None] = mapped_column(
        DateTime,
        nullable=True,
    )

    created_at: Mapped[datetime] = mapped_column(
        DateTime,
        default=utc_now,
        nullable=False,
    )

    @property
    def is_used(self) -> bool:
        """是否已被使用。

        由 `used_by_user_id` 派生，**不单独存一列** —— 见模块文档。
        """

        return self.used_by_user_id is not None

    def __repr__(self) -> str:
        state = (
            f"used_by={self.used_by_user_id}"
            if self.is_used
            else "unused"
        )

        return f"InviteCode(id={self.id}, code={self.code!r}, {state})"
