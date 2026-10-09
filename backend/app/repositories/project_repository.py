from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.project import Project


class ProjectRepository:
    """项目数据访问层"""

    def __init__(
        self,
        session: AsyncSession,
    ):
        self.session = session

    async def create(
        self,
        name: str,
        description: str | None,
        owner_id: int,
    ) -> Project:
        """创建项目"""

        project = Project(
            name=name,
            description=description,
            owner_id=owner_id,
        )

        self.session.add(project)

        await self.session.commit()

        await self.session.refresh(project)

        return project

    async def get_by_id(
        self,
        project_id: int,
    ) -> Project | None:
        """根据 ID 查询项目"""

        result = await self.session.execute(
            select(Project).where(
                Project.id == project_id
            )
        )

        return result.scalar_one_or_none()

    async def get_by_id_and_owner(
    self,
        project_id: int,
        owner_id: int,
    ) -> Project | None:
        """获取指定用户的项目"""

        result = await self.session.execute(
            select(Project).where(
                Project.id == project_id,
                Project.owner_id == owner_id,
            )
        )

        return result.scalar_one_or_none()

    async def get_by_owner_id(
        self,
        owner_id: int,
    ) -> list[Project]:
        """获取用户的所有项目"""

        result = await self.session.execute(
            select(Project)
            .where(
                Project.owner_id == owner_id
            )
            .order_by(
                Project.created_at.desc()
            )
        )

        return list(
            result.scalars().all()
        )

    async def update(
        self,
        project: Project,
        **kwargs,
    ) -> Project:
        """更新项目"""

        for field, value in kwargs.items():

            if hasattr(project, field):

                setattr(
                    project,
                    field,
                    value,
                )

        await self.session.commit()

        await self.session.refresh(project)

        return project

    async def delete(
        self,
        project: Project,
    ) -> None:
        """删除项目"""

        await self.session.delete(project)

        await self.session.commit()