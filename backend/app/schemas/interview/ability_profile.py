"""能力画像的响应契约。"""

from datetime import datetime

from pydantic import BaseModel, Field


class DimensionProfileResponse(BaseModel):
    """单个维度的画像。"""

    key: str
    label: str

    count: int

    # 中位数而不是平均值：实测分数分布偶尔是双峰的，
    # 均值会被单次极端值拖走（§8A.9 也是这个口径）。
    median: float | None = None

    minimum: int | None = None
    maximum: int | None = None
    latest: int | None = None

    # 极差。比标准差更直观，也是本项目一直用的口径。
    spread: int | None = None

    # 逐次分数，**按时间正序**（旧 → 新），供前端画趋势。
    history: list[int] = Field(default_factory=list)


class RecurringWeaknessResponse(BaseModel):
    """重复出现的弱点。"""

    text: str
    occurrences: int


class ProfileSessionResponse(BaseModel):
    """逐场明细里的一场。"""

    session_id: int
    project_id: int
    project_name: str | None = None
    target_role: str | None = None
    evaluated_at: datetime
    scores: dict[str, int]


class AbilityProfileResponse(BaseModel):
    """能力画像。

    ⚠️ 这个响应**故意不带**"优势 / 短板"的结论字段。
    判断交给调用方：服务端已经把中位数、区间、逐次明细与
    采样说明都给出来了，而"哪个算短板"取决于用户的目标岗位 ——
    那是产品判断，不该藏在聚合里。
    """

    session_count: int

    dimensions: list[DimensionProfileResponse] = Field(
        default_factory=list
    )

    sessions: list[ProfileSessionResponse] = Field(
        default_factory=list
    )

    recent_weaknesses: list[str] = Field(default_factory=list)
    recent_suggestions: list[str] = Field(default_factory=list)
    recurring_weaknesses: list[RecurringWeaknessResponse] = Field(
        default_factory=list
    )

    # 样本是否足以支撑"优势 / 短板"判断。
    sufficient_samples: bool = False

    # 采样与解读说明。必须暴露 —— 否则用户会把 10 分的差异
    # 当作真实进步/退步，而实测那可能就是评分波动。
    caveats: list[str] = Field(default_factory=list)
