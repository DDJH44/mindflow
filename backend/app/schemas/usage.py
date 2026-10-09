from pydantic import BaseModel, ConfigDict


class UsageMetricUsage(BaseModel):
    """单个指标的用量与额度。"""

    metric: str
    used: int
    quota: int
    remaining: int


class UsageResponse(BaseModel):
    """当前账期的完整额度视图。

    刻意返回**两份**额度而不是一份：任何一个先耗尽都会挡住用户。
    只报"剩余 3 场"会让人以为还能用 —— 实际可能题目额度已见底。
    """

    # 账期标识 "YYYY-MM"。
    # 暴露它而不是"剩余天数"：账期是权威口径，
    # 前端要显示倒计时可以自己算，但不应各算一套账期。
    period: str

    # 两个消费单位：场次与题目。
    interviews: UsageMetricUsage
    questions: UsageMetricUsage

    # 当前**受限方**：`"interviews"` / `"questions"` / `None`（均未受限）。
    #
    # 前端据此显示"本月面试场次已用完"或"本月题目额度已用完"，
    # 不必自己比较两个剩余量（比较规则只应存在于服务端）。
    limited_by: str | None = None

    # 是否还能继续开始面试。
    allowed: bool

    model_config = ConfigDict(from_attributes=True)
