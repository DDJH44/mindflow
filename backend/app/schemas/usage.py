from pydantic import BaseModel, ConfigDict


class UsageMetricUsage(BaseModel):
    """单个指标的用量与额度。"""

    metric: str
    used: int
    quota: int
    remaining: int


class InterviewCostPreview(BaseModel):
    """开一场面试的成本预估。

    ## 为什么需要它

    实测现状：用户只能看到"本月剩余 9 场、98 题"，却**不知道该设几题**。
    而额度是两份独立的约束，于是存在一个用户看不见的陷阱：

    > 剩余 2 场、8 题时，用户把题目上限设成 8 并开始面试 ——
    > 第 3 场能开，但**第 9 题会被拒**，面试在中间断掉。
    > 而那时场次已经花掉了，用户既没得到完整面试，也拿不回场次。

    也就是说 **"能不能开始"与"能不能开完"是两个不同的问题**，
    而原界面只回答了前者。

    ## 为什么消耗是确定的

    `decide_continue` 用 `questions_asked < max_questions` 判定继续，
    而 `questions_asked` **含追问**。因此"一场最多消耗
    `max_questions` 道题"是**上界而非估算** ——
    不需要历史数据、不需要统计模型。
    """

    # 规范化之后的题目预算（用户可能设了越界值）
    questions: int

    # 这一场需要的场次（恒为 1，列出是为了让调用方不需猜）
    interviews: int

    # 是否够开完
    fits: bool

    # 不够时的原因：
    # `insufficient_interviews`（开不了）
    # `insufficient_questions`（能开但会中途断掉）
    blocked_by: str | None = None

    # 会断在第几题（`insufficient_questions` 时才有值）
    truncates_at: int | None = None

    # 给用户看的一句话。**文案由服务端给** ——
    # 因为它依赖规范化后的预算，前端自己拼会拼错。
    message: str


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

    # 按**默认**题目预算（8 题）的预估。
    #
    # 前端在"开始面试"页显示它，用户改了题目数量后再带
    # `?max_questions=N` 重新请求。默认值放在这里是为了
    # 让"还没填表单"时也有话可说。
    cost_preview: InterviewCostPreview | None = None

    model_config = ConfigDict(from_attributes=True)
