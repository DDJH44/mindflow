from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.dependencies import get_current_user
from app.models.user import User
from app.schemas.user import UserResponse

from app.core.security import (
    create_access_token,
    hash_password,
    verify_password,
)
from app.database.session import get_db
from app.repositories.user_repository import UserRepository
from app.schemas.auth import (
    TokenResponse,
    UserLoginRequest,
    UserRegisterRequest,
)

router = APIRouter(
    prefix="/api/auth",
    tags=["Authentication"],
)


@router.post(
    "/register",
    status_code=status.HTTP_201_CREATED,
)
async def register(
    data: UserRegisterRequest,
    db: AsyncSession = Depends(get_db),
):

    user_repository = UserRepository(db)

    # 检查用户名
    existing_user = await user_repository.get_by_username(
        data.username
    )

    if existing_user:

        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="用户名已存在",
        )

    # 检查邮箱
    existing_email = await user_repository.get_by_email(
        data.email
    )

    if existing_email:

        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="邮箱已被注册",
        )

    # 密码加密
    password_hash = hash_password(
        data.password
    )

    # 创建用户
    user = await user_repository.create(
        username=data.username,
        email=data.email,
        password_hash=password_hash,
    )

    return {
        "message": "注册成功",
        "user": {
            "id": user.id,
            "username": user.username,
            "email": user.email,
        },
    }

@router.post(
    "/login",
    response_model=TokenResponse,
)
async def login(
    data: UserLoginRequest,
    db: AsyncSession = Depends(get_db),
):

    user_repository = UserRepository(db)

    # 根据用户名或邮箱查找用户
    user = await user_repository.get_by_account(
        data.account
    )

    # 用户不存在
    if not user:

        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="账号或密码错误",
        )

    # 密码错误
    if not verify_password(
        data.password,
        user.password_hash,
    ):

        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="账号或密码错误",
        )

    # 创建 Token
    access_token = create_access_token(
        data={
            "sub": str(user.id),
            "username": user.username,
        }
    )

    return {
        "access_token": access_token,
        "token_type": "bearer",
    }

@router.get(
    "/me",
        response_model=UserResponse,
)
async def get_me(
        current_user: User = Depends(get_current_user),
    ):
        """获取当前登录用户信息"""

        return current_user