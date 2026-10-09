"""面试会话状态机。

对应 MIND_FLOW_PLAN.md §10。设计原则（ADR-010）：
状态机优先于多 Agent 拆分 —— 保证会话可恢复、可审计，控制复杂度。

状态流转：

    draft
      → preparing_context
      → planned
      → asking
      → waiting_for_answer
      → evaluating
      ├─ asking（追问或下一题）
      └─ summarizing → completed

    任何可恢复状态 → paused
    任何未完成状态   → cancelled / failed

这里的职责只有两件事：**定义合法状态** 与 **校验转移是否合法**。
不做数据库访问，因此可以脱离数据库做完整单元测试。
"""

from enum import Enum


class TransitionTrigger(str, Enum):
    """状态转移的触发原因。

    记录原因而不是只记"状态变了"：
    排查时第一个问题永远是"谁把它改到这里的"。
    只有起止状态时，`asking → asking` 这种跨度无法解释 ——
    它可能来自"答完一题继续问"，也可能来自"跳过一轮"。

    ⚠️ 不要与 `interview_termination.TerminationReason` 混用：
    那个回答"面试为什么结束"，本枚举回答"这次状态变化由什么引起"。
    """

    # 开始面试（draft → preparing_context → planned）
    INTERVIEW_STARTED = "interview_started"

    # 生成题目后进入提问态
    QUESTION_GENERATED = "question_generated"

    # 候选人提交作答
    ANSWER_SUBMITTED = "answer_submitted"

    # 分析完成后进入评价态
    ANALYSIS_COMPLETED = "analysis_completed"

    # 追问已生成，交回提问态
    FOLLOW_UP_GENERATED = "follow_up_generated"

    # 整场评价开始 / 完成
    EVALUATION_STARTED = "evaluation_started"
    EVALUATION_COMPLETED = "evaluation_completed"

    # 题量达到预算，自动结束
    BUDGET_EXHAUSTED = "budget_exhausted"

    # 用户主动暂停 / 恢复 / 结束
    #
    # 注意这里**没有** user_cancelled：取消目前只能由通用
    # transition 端点触发，走的是 UNSPECIFIED。
    # 等真的有"取消面试"这个动作时再加，避免留下永不被赋值的成员。
    USER_PAUSED = "user_paused"
    USER_RESUMED = "user_resumed"
    USER_FINISHED = "user_finished"

    # 未说明来源。
    #
    # 默认值刻意不叫 "unknown"：它表示"调用方没有提供原因"，
    # 而不是"原因无法确定"。二者在排查时含义不同。
    UNSPECIFIED = "unspecified"


class SessionStatus(str, Enum):
    """面试会话状态。

    使用 str 混入，使枚举值可直接存入数据库的 String 列，
    并与既有的字符串状态兼容（见 LEGACY_STATUS_MAP）。
    """

    DRAFT = "draft"
    PREPARING_CONTEXT = "preparing_context"
    PLANNED = "planned"
    ASKING = "asking"
    WAITING_FOR_ANSWER = "waiting_for_answer"
    EVALUATING = "evaluating"
    SUMMARIZING = "summarizing"
    COMPLETED = "completed"

    # 可恢复
    PAUSED = "paused"

    # 终止
    CANCELLED = "cancelled"
    FAILED = "failed"


# 终态：不再允许任何转移
TERMINAL_STATUSES: frozenset[SessionStatus] = frozenset(
    {
        SessionStatus.COMPLETED,
        SessionStatus.CANCELLED,
        SessionStatus.FAILED,
    }
)

# 主流程转移表。
#
# 注意 evaluating 的后继是 asking 而不是分别写 follow_up /
# next_question：那两个是**行为分支**，不是独立状态，
# 落库时都表现为"回到 asking"。这样状态数保持可控，
# 行为分支由业务层决定（见 §10 的状态表）。
TRANSITIONS: dict[SessionStatus, frozenset[SessionStatus]] = {
    SessionStatus.DRAFT: frozenset(
        {
            SessionStatus.PREPARING_CONTEXT,
            SessionStatus.CANCELLED,
        }
    ),
    SessionStatus.PREPARING_CONTEXT: frozenset(
        {
            SessionStatus.PLANNED,
            SessionStatus.FAILED,
            SessionStatus.CANCELLED,
            SessionStatus.PAUSED,
        }
    ),
    SessionStatus.PLANNED: frozenset(
        {
            SessionStatus.ASKING,
            SessionStatus.PAUSED,
            SessionStatus.CANCELLED,
        }
    ),
    SessionStatus.ASKING: frozenset(
        {
            SessionStatus.WAITING_FOR_ANSWER,
            SessionStatus.PAUSED,
            SessionStatus.FAILED,
        }
    ),
    SessionStatus.WAITING_FOR_ANSWER: frozenset(
        {
            SessionStatus.EVALUATING,
            SessionStatus.PAUSED,
            SessionStatus.CANCELLED,
        }
    ),
    SessionStatus.EVALUATING: frozenset(
        {
            # 追问或下一题：都回到 asking
            SessionStatus.ASKING,
            SessionStatus.SUMMARIZING,
            SessionStatus.PAUSED,
            SessionStatus.FAILED,
        }
    ),
    SessionStatus.SUMMARIZING: frozenset(
        {
            SessionStatus.COMPLETED,
            SessionStatus.FAILED,
        }
    ),
    SessionStatus.PAUSED: frozenset(
        {
            # 这里刻意只放 cancelled。
            #
            # paused 的**实际**可恢复目标由 resume_status 动态决定
            # （见 allowed_transitions）。不放一个"看起来合理"的静态集合，
            # 是为了避免调用方误以为可以无条件恢复到 asking ——
            # 那会把处于 waiting_for_answer 的会话错误地拉回提问态。
            SessionStatus.CANCELLED,
        }
    ),
    # 终态
    SessionStatus.COMPLETED: frozenset(),
    SessionStatus.CANCELLED: frozenset(),
    SessionStatus.FAILED: frozenset(),
}


# 旧状态值到新状态的映射。
#
# 迁移前会话用 "created" 表示刚建立，语义上等于 draft。
# 保留这张表使历史数据可被正确解读，不需要数据回填。
LEGACY_STATUS_MAP: dict[str, SessionStatus] = {
    "created": SessionStatus.DRAFT,
}


# 允许被暂停、并在恢复时回到原状态的状态集合。
#
# 暂停必须记住"从哪来"，否则恢复时只能一律回到 asking ——
# 那会把处于 waiting_for_answer 的会话错误地拉回提问态。
PAUSABLE_STATUSES: frozenset[SessionStatus] = frozenset(
    {
        SessionStatus.PREPARING_CONTEXT,
        SessionStatus.PLANNED,
        SessionStatus.ASKING,
        SessionStatus.WAITING_FOR_ANSWER,
        SessionStatus.EVALUATING,
    }
)


class InvalidTransitionError(ValueError):
    """非法状态转移。"""

    def __init__(
        self,
        current: SessionStatus,
        target: SessionStatus,
        resume_status: str | SessionStatus | None = None,
    ):
        self.current = current
        self.target = target
        self.resume_status = resume_status
        allowed = sorted(
            item.value
            for item in allowed_transitions(current, resume_status)
        )
        hint = ""
        if current is SessionStatus.PAUSED and resume_status is None:
            hint = "（paused 会话缺少 resume_status，只能取消）"

        super().__init__(
            f"非法状态转移：{current.value} → {target.value}；"
            f"允许的目标状态为 {allowed}{hint}"
        )


def normalize_status(raw: str | SessionStatus) -> SessionStatus:
    """把数据库中的状态字符串规范化为枚举。

    未知值直接抛错而不是静默兜底：
    状态值写错会让会话卡在无法解释的状态里，
    这类问题必须在写入时就暴露。
    """

    if isinstance(raw, SessionStatus):
        return raw

    if raw in LEGACY_STATUS_MAP:
        return LEGACY_STATUS_MAP[raw]

    try:
        return SessionStatus(raw)
    except ValueError as exc:
        raise ValueError(
            f"未知的会话状态: {raw!r}；"
            f"合法值为 {sorted(item.value for item in SessionStatus)}"
        ) from exc


def allowed_transitions(
    current: str | SessionStatus,
    resume_status: str | SessionStatus | None = None,
) -> frozenset[SessionStatus]:
    """返回当前状态允许转移到的状态集合。

    `resume_status` 只在 current 为 `paused` 时有意义：
    暂停必须记住"从哪来"，恢复时回到那个状态。
    若未提供，则退化为 TRANSITIONS 里的静态集合
    （只允许取消）—— 这比默默允许回到任意状态更安全。
    """

    normalized = normalize_status(current)

    if normalized is SessionStatus.PAUSED:
        return _paused_targets(resume_status)

    return TRANSITIONS[normalized]


def _paused_targets(
    resume_status: str | SessionStatus | None,
) -> frozenset[SessionStatus]:
    """计算 paused 允许转移到的状态。"""

    if resume_status is None:
        # 没有恢复目标：只允许取消。
        # 不猜一个默认状态，避免把会话恢复到错误的位置。
        return frozenset({SessionStatus.CANCELLED})

    try:
        normalized_resume = normalize_status(resume_status)
    except ValueError:
        return frozenset({SessionStatus.CANCELLED})

    if normalized_resume not in PAUSABLE_STATUSES:
        return frozenset({SessionStatus.CANCELLED})

    return frozenset(
        {normalized_resume, SessionStatus.CANCELLED}
    )


def resolve_resume_target(
    resume_status: str | SessionStatus | None,
    answered_any: bool,
    has_unanswered_question: bool,
) -> SessionStatus | None:
    """确定 paused 会话应当恢复到哪个状态。

    优先用存下来的 `resume_status`；**缺失时按问答事实推断**。

    为什么需要推断：`resume_status` 为空且状态为 `paused` 的会话
    在状态机里只能取消 —— 等于**永久卡死**。实测这类会话无法恢复、
    也无法结束（用户点"结束并生成报告"会得到
    `非法状态转移：paused → waiting_for_answer`）。

    推断有确定的依据，不是猜：
    - 存在**未作答的题目** → 当初停在 `asking`
      （题目已发给候选人，还没收回答）
    - 否则 → 当初停在 `waiting_for_answer`
      （回答已收到，正处于等待处理/评价的过渡态）

    注意这只是**恢复目标**的推断；能否真的转移仍由
    `allowed_transitions` 校验，因此推断错了也不会写入非法状态。

    返回 None 表示无法推断（例如会话一题都没生成）。
    """

    if resume_status is not None:
        try:
            normalized = normalize_status(resume_status)
        except ValueError:
            normalized = None

        if normalized in PAUSABLE_STATUSES:
            return normalized

    if has_unanswered_question:
        return SessionStatus.ASKING

    if answered_any:
        return SessionStatus.WAITING_FOR_ANSWER

    return None


def can_transition(
    current: str | SessionStatus,
    target: str | SessionStatus,
    resume_status: str | SessionStatus | None = None,
) -> bool:
    """判断转移是否合法。"""

    return normalize_status(target) in allowed_transitions(
        current, resume_status
    )


def assert_transition(
    current: str | SessionStatus,
    target: str | SessionStatus,
    resume_status: str | SessionStatus | None = None,
) -> SessionStatus:
    """校验转移，不合法时抛出 InvalidTransitionError。"""

    normalized_current = normalize_status(current)
    normalized_target = normalize_status(target)

    if normalized_target not in allowed_transitions(
        normalized_current, resume_status
    ):
        raise InvalidTransitionError(
            normalized_current,
            normalized_target,
            resume_status=resume_status,
        )

    return normalized_target


def is_terminal(status: str | SessionStatus) -> bool:
    """是否为终态。"""

    return normalize_status(status) in TERMINAL_STATUSES
