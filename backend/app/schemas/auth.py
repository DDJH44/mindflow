from pydantic import BaseModel, EmailStr, Field


class UserRegisterRequest(BaseModel):

    username: str = Field(
        min_length=3,
        max_length=50,
    )

    email: EmailStr

    password: str = Field(
        min_length=6,
        max_length=100,
    )

    # 邀请码。仅在 `settings.registration_require_invite` 为 True
    # 时必填 —— 因此这里**设为可选**，由服务端判定是否缺少。
    #
    # 为什么不在 schema 上写死必填：那会让本地开发与测试
    # 的每一个注册请求都必须带码，而那是另一个开关才决定的事。
    # schema 只管格式，开关管策略。
    invite_code: str | None = Field(
        default=None,
        max_length=64,
        description="邀请码。部署方开启注册控制时必填",
    )


class UserLoginRequest(BaseModel):

    account: str = Field(
        min_length=3,
        max_length=100,
        description="用户名或邮箱",
    )

    password: str = Field(
        min_length=6,
        max_length=100,
    )


class TokenResponse(BaseModel):

    access_token: str

    token_type: str = "bearer"