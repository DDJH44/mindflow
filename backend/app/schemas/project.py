from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field


class ProjectCreateRequest(BaseModel):
    """创建项目请求"""

    name: str = Field(
        ...,
        min_length=1,
        max_length=100,
        description="项目名称",
    )

    description: str | None = Field(
        default=None,
        description="项目描述",
    )


class ProjectResponse(BaseModel):
    """项目响应"""

    id: int

    name: str

    description: str | None

    owner_id: int

    status: str

    created_at: datetime

    updated_at: datetime

    model_config = ConfigDict(
        from_attributes=True
    )

class ProjectUpdateRequest(BaseModel):
    """更新项目请求"""

    name: str | None = Field(
        default=None,
        min_length=1,
        max_length=100,
        description="项目名称",
    )

    description: str | None = Field(
        default=None,
        description="项目描述",
    )

    status: str | None = Field(
        default=None,
        description="项目状态",
    )