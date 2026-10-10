from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.logging import set_user_id
from app.core.security import decode_access_token
from app.database.session import get_db
from app.repositories.user_repository import UserRepository


security = HTTPBearer()


async def get_current_user(
    credentials: HTTPAuthorizationCredentials = Depends(security),
    db: AsyncSession = Depends(get_db),
):
    """获取当前登录用户"""

    token = credentials.credentials

    payload = decode_access_token(token)

    if not payload:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="无效或已过期的 Token",
        )

    user_id = payload.get("sub")

    if not user_id:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Token 无效",
        )

    try:
        user_id = int(user_id)

    except ValueError:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Token 无效",
        )

    user_repository = UserRepository(db)

    user = await user_repository.get_by_id(user_id)

    if not user:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="用户不存在",
        )

    # 把身份写进日志上下文，让此后所有日志都带 `user=…`。
    #
    # 为什么在这里而不是中间件：中间件阶段拿不到用户
    # （JWT 还没解），而在中间件里自己再解一次会让认证逻辑
    # 出现第二种实现。这里本来就已经解出了用户，顺手绑定最省。
    set_user_id(user.id)

    return user