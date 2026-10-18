from fastapi import APIRouter, Depends, HTTPException, Request, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.core import rate_limit
from app.core.config import settings
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
from app.services import invite_service

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
    request: Request,
    db: AsyncSession = Depends(get_db),
):
    # 限流：按 IP。
    #
    # **注册是最需要限的一条** —— 无限注册是"反复触发昂贵操作"
    # （上传材料 → 切块 → 嵌入调用）的起点。
    # 没有它，D67 的体积上限只挡得住"单个文件很大"，
    # 却挡不住"注册很多账号、每个都传满额度的文件"。
    if settings.rate_limit_enabled:
        await rate_limit.enforce(
            rate_limit.RateLimit(
                times=settings.rate_limit_register_times,
                seconds=settings.rate_limit_register_seconds,
                scope="register",
                by="ip",
            ),
            request,
        )

    user_repository = UserRepository(db)

    # 邀请码：先做**格式与策略**检查，再动数据库。
    #
    # 顺序理由：邀请码不对时不该先花一次"用户名是否已存在"的查询，
    # 更不该让攻击者通过"用户名已存在"这个错误反推
    # 哪些用户名被占了。
    invite_code = None

    if settings.registration_require_invite:
        if not data.invite_code or not data.invite_code.strip():
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="本次部署仅限持邀请码的用户注册",
            )

        invite_code = data.invite_code

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

    if invite_code is None:
        # 未开启邀请制：沿用原来的直接创建。
        user = await user_repository.create(
            username=data.username,
            email=data.email,
            password_hash=password_hash,
        )
    else:
        # 开启邀请制：建账号与占码在**同一个事务**里完成。
        #
        # 失败时整体回滚 —— 不会留下"没有邀请码的账号"，
        # 也不会白白作废一条码。理由见
        # `app/services/invite_service.py` 的模块文档。
        try:
            user = await invite_service.register_with_invite(
                db,
                username=data.username,
                email=data.email,
                password_hash=password_hash,
                code=invite_code,
            )
        except invite_service.InviteCodeError as exc:
            # 回滚：上面可能已经 flush 了一条 INSERT。
            await db.rollback()

            # 403 而不是 400：这不是"请求格式错误"，
            # 而是"你没有权限用这个邀请码"。
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=str(exc),
            ) from exc

    return {
        "message": "注册成功",
        "user": {
            "id": user.id,
            "username": user.username,
            "email": user.email,
        },
    }


@router.get("/registration-mode")
async def get_registration_mode():
    """告诉前端"注册是否需要邀请码"。

    为什么需要这个端点：邀请码输入框应当**只在需要时显示**。
    若前端一律显示，本地开发时会看到一个多余的必填项；
    若一律不显示，开启邀请制后用户根本不知道要填什么 ——
    而那时注册会失败，且失败原因看起来像"服务器坏了"。

    这个端点不含敏感信息（只暴露一个布尔值），
    因此不需要认证 —— 登录页本来就是未登录状态。
    """

    return {
        "require_invite": settings.registration_require_invite,
    }

@router.post(
    "/login",
    response_model=TokenResponse,
)
async def login(
    data: UserLoginRequest,
    request: Request,
    db: AsyncSession = Depends(get_db),
):
    # 限流：按 IP。防**暴力破解口令** ——
    # 没有它，脚本可以每秒试上千次密码。
    if settings.rate_limit_enabled:
        await rate_limit.enforce(
            rate_limit.RateLimit(
                times=settings.rate_limit_login_times,
                seconds=settings.rate_limit_login_seconds,
                scope="login",
                by="ip",
            ),
            request,
        )

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