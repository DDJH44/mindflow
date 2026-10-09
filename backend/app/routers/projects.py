from fastapi import (
    APIRouter,
    Depends,
    HTTPException,
    status,
)
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.dependencies import get_current_user
from app.database.session import get_db
from app.models.user import User
from app.repositories.project_repository import ProjectRepository
from app.schemas.project import (
    ProjectCreateRequest,
    ProjectResponse,
    ProjectUpdateRequest,
)


router = APIRouter(
    prefix="/api/projects",
    tags=["Projects"],
)


@router.post(
    "",
    response_model=ProjectResponse,
    status_code=status.HTTP_201_CREATED,
)
async def create_project(
    data: ProjectCreateRequest,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):

    project_repository = ProjectRepository(
        db
    )

    project = await project_repository.create(
        name=data.name,
        description=data.description,
        owner_id=current_user.id,
    )

    return project


@router.get(
    "",
    response_model=list[ProjectResponse],
)
async def get_my_projects(
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """获取当前用户的所有项目"""

    project_repository = ProjectRepository(
        db
    )

    projects = await project_repository.get_by_owner_id(
        current_user.id
    )

    return projects

@router.get(
    "/{project_id}",
    response_model=ProjectResponse,
)
async def get_project_detail(
    project_id: int,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """获取项目详情"""

    project_repository = ProjectRepository(
        db
    )

    project = await project_repository.get_by_id_and_owner(
        project_id=project_id,
        owner_id=current_user.id,
    )

    if not project:

        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="项目不存在",
        )

    return project

@router.put(
    "/{project_id}",
    response_model=ProjectResponse,
)
async def update_project(
    project_id: int,
    data: ProjectUpdateRequest,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):

    project_repository = ProjectRepository(
        db
    )

    project = await project_repository.get_by_id_and_owner(
        project_id=project_id,
        owner_id=current_user.id,
    )

    if not project:

        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="项目不存在",
        )

    update_data = data.model_dump(
        exclude_unset=True
    )

    updated_project = await project_repository.update(
        project,
        **update_data,
    )

    return updated_project

@router.delete(
    "/{project_id}",
    status_code=status.HTTP_204_NO_CONTENT,
)
async def delete_project(
    project_id: int,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """删除项目"""

    project_repository = ProjectRepository(db)

    # 查询项目，同时确保项目属于当前用户
    project = await project_repository.get_by_id_and_owner(
        project_id=project_id,
        owner_id=current_user.id,
    )

    if not project:

        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="项目不存在",
        )

    # 删除项目
    await project_repository.delete(project)