from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from app.database.session import get_db
from app.repositories.user_repository import UserRepository
from app.schemas.user import UserCreate, UserResponse


router = APIRouter(
    prefix="/api/users",
    tags=["Users"],
)


@router.post(
    "",
    response_model=UserResponse,
)
async def create_user(
    user_data: UserCreate,
    session: AsyncSession = Depends(get_db),
):

    repository = UserRepository(session)

    user = await repository.create(
        username=user_data.username,
        email=user_data.email,
        password_hash=user_data.password,
    )

    return user