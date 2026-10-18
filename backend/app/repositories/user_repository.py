from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.user import User


class UserRepository:
    """用户数据访问层"""

    def __init__(self, session: AsyncSession):
        self.session = session

    async def get_by_id(self, user_id: int) -> User | None:
        """根据 ID 查询用户"""

        result = await self.session.execute(
            select(User).where(User.id == user_id)
        )

        return result.scalar_one_or_none()

    async def get_by_username(self, username: str) -> User | None:
        """根据用户名查询用户"""

        result = await self.session.execute(
            select(User).where(User.username == username)
        )

        return result.scalar_one_or_none()

    async def get_by_email(self, email: str) -> User | None:
        """根据邮箱查询用户"""

        result = await self.session.execute(
            select(User).where(User.email == email)
        )

        return result.scalar_one_or_none()
    
    async def get_by_account(
            self,
            account: str,
        ) -> User | None:

            result = await self.session.execute(
                select(User).where(
                    or_(
                        User.username == account,
                        User.email == account,
                    )
                )
            )

            return result.scalar_one_or_none()

    async def add(
        self,
        username: str,
        email: str,
        password_hash: str,
    ) -> User:
        """把用户**加入事务**，但**不提交**。

        与 `create()` 的区别只有提交这一步 —— 而这一步正是
        "邀请码 + 建账号"能不能原子的关键：

        `create()` 会立刻提交，于是一旦占码失败，
        用户已经落库了 —— 库里多出一个**没有邀请码的账号**，
        而它带着完整的 10 场额度。发码的人根本不知道多了一个。

        `flush()` 只把 INSERT 发给数据库（从而拿到 `id`），
        事务仍然开着。之后占码失败时整体回滚，
        上面那条 INSERT 一并撤销。

        ⚠️ 用这个方法后，**提交责任在调用方** ——
        忘记 `commit()` 会导致数据静默丢失。
        """

        user = User(
            username=username,
            email=email,
            password_hash=password_hash,
        )

        self.session.add(user)

        # flush 而不是 commit：把 INSERT 发出去拿到 id，
        # 但事务边界留给调用方。
        await self.session.flush()

        return user

    async def create(
        self,
        username: str,
        email: str,
        password_hash: str,
    ) -> User:
        """创建用户并**立即提交**。

        适用于不需要与其他写入保持原子的场景。
        "邀请码 + 建账号"请用 `add()`（见其说明）。
        """

        user = await self.add(
            username=username,
            email=email,
            password_hash=password_hash,
        )

        await self.session.commit()

        await self.session.refresh(user)

        return user