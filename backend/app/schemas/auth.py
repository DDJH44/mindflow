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