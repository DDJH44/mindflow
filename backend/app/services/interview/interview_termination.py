"""面试终止决策。

职责：回答"这一轮之后还应该继续问吗"。

为什么单独成模块而不是塞进服务里：
终止规则是**纯逻辑**（不访问数据库、不调 LLM），
因此可以脱离环境完整测试 —— 这与状态机同样的取舍（ADR-022）。

为什么必须有终止条件：
MIND_FLOW_PLAN.md §9.3 要求"每轮题目预算、主题覆盖与终止条件
必须由 `InterviewSession` 持久化"。在此之前面试只能靠用户
主动点结束，没有服务端强制的边界 —— 既不控成本，也无法定义
"一轮面试"何时完成。
"""

from enum import Enum


class TerminationReason(str, Enum):
    """面试结束的原因。

    必须记录原因而不是只记"已结束"：
    - 业务侧要区分"额度用完"与"用户自己关了"，两者的后续动作不同
    - 复盘时要能回答"这场面试为什么停在第 6 题"
    """

    # 达到题目预算上限（服务端硬限制）
    BUDGET_EXHAUSTED = "budget_exhausted"

    # 用户主动结束
    USER_FINISHED = "user_finished"

    # 遇到错误而中止
    FAILED = "failed"

    # 用户取消
    CANCELLED = "cancelled"


# 题目预算的默认值。
#
# 取 8 的理由：
# - 太短（如 3）测不出候选人的技术深度，追问链铺不开
# - 太长（如 20）单场成本高，且候选人疲劳后回答质量下降，
#   评分的区分度反而变差
# 该值是**产品默认值**，可被单个会话覆盖（`InterviewSession.max_questions`）。
DEFAULT_MAX_QUESTIONS = 8

# 允许的取值范围。上限用于防止误配置导致成本失控 ——
# 每道题的追问都要调 LLM，一个 1000 的预算能把额度烧穿。
MIN_ALLOWED_QUESTIONS = 1
MAX_ALLOWED_QUESTIONS = 30


class ContinueDecision:
    """"是否继续"的判断结果。

    同时携带原因，避免调用方拿到 False 之后再去猜为什么。
    """

    __slots__ = ("should_continue", "reason", "detail")

    def __init__(
        self,
        should_continue: bool,
        reason: TerminationReason | None = None,
        detail: str = "",
    ):
        self.should_continue = should_continue
        self.reason = reason
        self.detail = detail

    def __repr__(self) -> str:
        return (
            f"ContinueDecision(should_continue="
            f"{self.should_continue}, reason="
            f"{self.reason.value if self.reason else None}, "
            f"detail={self.detail!r})"
        )

    def __eq__(self, other) -> bool:
        return (
            isinstance(other, ContinueDecision)
            and self.should_continue == other.should_continue
            and self.reason == other.reason
        )


def normalize_max_questions(value: int | None) -> int:
    """把题目预算规范化到允许范围内。

    越界值**夹取**而不是报错：
    预算是可配置项，配置错误不应该让整场面试无法开始。
    但夹取必须记录在案（调用方可通过返回值察觉），
    因此这里只做夹取，不做静默兜底到默认值。
    """

    if value is None:
        return DEFAULT_MAX_QUESTIONS

    if value < MIN_ALLOWED_QUESTIONS:
        return MIN_ALLOWED_QUESTIONS

    if value > MAX_ALLOWED_QUESTIONS:
        return MAX_ALLOWED_QUESTIONS

    return value


def decide_continue(
    questions_asked: int,
    max_questions: int,
) -> ContinueDecision:
    """判断是否还应该继续出题。

    `questions_asked` 是**已经问过的题目总数**（含追问）。
    追问也计入预算，因为它同样消耗一次 LLM 分析与生成 ——
    若不计入，一条长追问链会把成本推到远超预期。
    """

    budget = normalize_max_questions(max_questions)

    if questions_asked < budget:
        return ContinueDecision(
            should_continue=True,
            detail=(
                f"已问 {questions_asked} / 预算 {budget}，继续"
            ),
        )

    return ContinueDecision(
        should_continue=False,
        reason=TerminationReason.BUDGET_EXHAUSTED,
        detail=(
            f"已达题目预算：{questions_asked} / {budget}"
        ),
    )
