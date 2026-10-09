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

    async def create(
        self,
        username: str,
        email: str,
        password_hash: str,
    ) -> User:
        """创建用户"""

        user = User(
            username=username,
            email=email,
            password_hash=password_hash,
        )

        self.session.add(user)

        await self.session.commit()

        await self.session.refresh(user)

        return user