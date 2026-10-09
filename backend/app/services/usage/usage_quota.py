"""用量指标与配额判定。

职责：回答"这个用户这个周期还能不能再用"。

这是**纯逻辑**（不访问数据库、不调 LLM），
因此可以脱离环境完整测试 —— 与状态机、终止决策同样的取舍。

关键设计：判定与实际计数**必须原子**，否则并发请求会一起通过校验。
本模块只负责"算出结论"，原子性由调用方用一个 UPSERT
先自增再判定来保证（见 app/services/usage_service.py）。
"""

from datetime import datetime, timezone
from enum import Enum


class UsageMetric(str, Enum):
    """可计量的用量指标。

    只列出**已经被消费掉、无法回收**的动作。
    单位为"场"与"题"两种：场是用户视角的消费品，
    题是成本视角的计量单位（成本主要发生在逐题的 LLM 调用上）。
    """

    # 开始一场面试。
    #
    # 注意：消费发生在**开始面试**时，而不是创建会话时。
    # 创建会话只产生一个 draft 草稿，没有任何 LLM 调用；
    # 若在创建时扣额度，"点了新建又没开始"就会白损失一场。
    INTERVIEW_STARTED = "interview_started"

    # 生成一道题目（含追问）。
    #
    # 按题计量的理由：单场题目上限（`max_questions`）描述的是
    # "这一场想聊多深"，不是成本额度。若只有单场上限，
    # 用户每场都设 30 题就等于把上限变成了授权。
    #
    # 追问也计入：它同样消耗一次 LLM 分析 + 一次生成。
    QUESTION_GENERATED = "question_generated"


# 每用户每月的默认面试场次上限。
#
# 取 10 的理由：
# - 免费用户一个月练 10 场足够形成使用习惯，又不会让 LLM 成本失控
# - 单场约 8 题（默认预算），每题 2~4 次 LLM 调用，
#   10 场约 160~320 次调用 —— 在可接受的成本量级内
DEFAULT_INTERVIEW_QUOTA = 10

# 每用户每月的默认题目总数上限（含追问，跨所有场次）。
#
# 取 100 的理由：默认 10 场 × 默认 8 题 = 80，留约 25% 余量，
# 让"少开几场但每场深聊"的用法不被误伤。
DEFAULT_QUESTION_QUOTA = 100

# 允许配置的范围。
#
# 上限用于防止误配置把成本放开 —— 配额存在的意义就是封顶，
# 一个 999999 的配额等同于没有配额。
MAX_ALLOWED_QUOTA = 1000

# 题目额度的允许上限取得更大：它的单位是"题"而不是"场"，
# 合理量级本来就高一个数量级。
MAX_ALLOWED_QUESTION_QUOTA = 5000


class QuotaExceeded(Exception):
    """配额不足。

    用异常而不是返回布尔值：配额不足是**调用方的错误**
    （应当转成 HTTP 429），静默返回 False 容易被忽略，
    最后表现成"莫名其妙创建不了会话"。
    """

    def __init__(
        self,
        metric: UsageMetric,
        period: str,
        used: int,
        quota: int,
    ):
        self.metric = metric
        self.period = period
        self.used = used
        self.quota = quota

        super().__init__(
            f"本月「{metric.value}」额度已用完："
            f"{used} / {quota}（周期 {period}）；"
            f"下个周期重置"
        )


class QuotaDecision:
    """配额判定结果。"""

    __slots__ = ("allowed", "used", "quota", "period", "remaining")

    def __init__(
        self,
        allowed: bool,
        used: int,
        quota: int,
        period: str,
    ):
        self.allowed = allowed
        self.used = used
        self.quota = quota
        self.period = period
        self.remaining = max(quota - used, 0)

    def __repr__(self) -> str:
        return (
            f"QuotaDecision(allowed={self.allowed}, used={self.used}, "
            f"quota={self.quota}, period={self.period!r})"
        )

    def __eq__(self, other) -> bool:
        return (
            isinstance(other, QuotaDecision)
            and self.allowed == other.allowed
            and self.used == other.used
            and self.quota == other.quota
            and self.period == other.period
        )


def current_period(now: datetime | None = None) -> str:
    """当前账期，格式 "YYYY-MM"。

    账期在**写入时**确定，不由读取时重新计算 ——
    否则跨月的那一瞬间，校验读到的账期与自增写入的账期可能不同，
    导致额度被绕过或提前重置。

    统一用 UTC：服务器时区变化不应改变用户的账期。
    """

    moment = now or datetime.now(timezone.utc)

    return f"{moment.year:04d}-{moment.month:02d}"


def _normalize(value: int | None, default: int, ceiling: int) -> int:
    """把配额夹取到允许范围。

    与题目预算同样的取舍（见 interview_termination）：
    配置错误不应该让整个功能不可用，但也不能让误填的大数字
    把成本放开。因此**夹取**而不是报错。
    """

    if value is None:
        return default

    if value < 0:
        return 0

    if value > ceiling:
        return ceiling

    return value


def normalize_quota(value: int | None) -> int:
    """规范化**场次**配额。"""

    return _normalize(
        value, DEFAULT_INTERVIEW_QUOTA, MAX_ALLOWED_QUOTA
    )


def normalize_question_quota(value: int | None) -> int:
    """规范化**题目**配额。

    上限与场次配额的量级不同（题 vs 场），因此各自有上限。
    """

    return _normalize(
        value,
        DEFAULT_QUESTION_QUOTA,
        MAX_ALLOWED_QUESTION_QUOTA,
    )


class QuotaPair:
    """两份额度的合成视图。

    为什么需要它：场次与题目是两种独立的约束，
    任何一个先耗尽都会挡住用户。只报"剩余 3 场"
    会让人以为还能用 —— 实际可能题目额度已经见底。

    因此对外必须成对提供，并明确指出**当前受限方**。
    """

    __slots__ = (
        "period",
        "interviews_used",
        "interview_quota",
        "interviews_remaining",
        "questions_used",
        "question_quota",
        "questions_remaining",
    )

    def __init__(
        self,
        period: str,
        interviews_used: int,
        interview_quota: int,
        questions_used: int,
        question_quota: int,
    ):
        self.period = period
        self.interviews_used = interviews_used
        self.interview_quota = interview_quota
        self.interviews_remaining = max(
            interview_quota - interviews_used, 0
        )
        self.questions_used = questions_used
        self.question_quota = question_quota
        self.questions_remaining = max(
            question_quota - questions_used, 0
        )

    @property
    def limited_by(self) -> str | None:
        """当前受限方。

        返回 `"interviews"` / `"questions"`，都不受限时为 None。
        取**更紧**的那个：剩余场次不足以再开一场，
        或剩余题数不足以再出一题。
        """

        if self.interviews_remaining <= 0:
            return "interviews"

        if self.questions_remaining <= 0:
            return "questions"

        return None

    @property
    def allowed(self) -> bool:
        return self.limited_by is None

    def __repr__(self) -> str:
        return (
            f"QuotaPair(period={self.period!r}, "
            f"interviews={self.interviews_used}/"
            f"{self.interview_quota}, "
            f"questions={self.questions_used}/"
            f"{self.question_quota}, "
            f"limited_by={self.limited_by!r})"
        )


def decide_quota(
    used: int,
    quota: int,
    period: str,
) -> QuotaDecision:
    """判断是否还能继续使用。

    `used` 应当是**已经包含本次动作在内**的数量
    （即先自增再判定）。这样并发请求各自拿到不同的 used，
    只有真正超出的那个会被拒绝 —— 若先判定再自增，
    两个并发请求会同时看到 used=9 / quota=10 并一起通过。

    因为 used 含本次动作，所以**空闲时查询用量会看到
    `used=0` 且 `allowed=True`**（尚未消费过）；
    而 `quota=0` 表示不允许任何消费，此时 `used=0` 也应判为不可用。
    """

    normalized = normalize_quota(quota)

    # quota=0 是"完全不允许"的意思。
    # 若只比较 `used <= quota`，`used=0, quota=0` 会被判为可用 ——
    # 那与"零额度"的语义直接矛盾。
    if normalized == 0:
        return QuotaDecision(
            allowed=False,
            used=used,
            quota=normalized,
            period=period,
        )

    return QuotaDecision(
        allowed=used <= normalized,
        used=used,
        quota=normalized,
        period=period,
    )


def decide_question_quota(
    used: int,
    quota: int,
    period: str,
) -> QuotaDecision:
    """判断题目额度是否还能继续使用。

    与 `decide_quota` 分开是因为上限量级不同
    （题 vs 场），共用会让其中一边的夹取范围不合理。
    """

    normalized = normalize_question_quota(quota)

    if normalized == 0:
        return QuotaDecision(
            allowed=False,
            used=used,
            quota=normalized,
            period=period,
        )

    return QuotaDecision(
        allowed=used <= normalized,
        used=used,
        quota=normalized,
        period=period,
    )
