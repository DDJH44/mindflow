"""面试问答相关的请求与响应契约。"""

from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field


class InterviewAnswerCreate(BaseModel):
    """提交一道题的作答。"""

    question_id: int = Field(
        description="作答针对的问题 ID，必须属于路径上的会话",
    )

    answer: str = Field(
        min_length=1,
        description="候选人的回答文本",
    )


class InterviewQuestionResponse(BaseModel):
    """一道面试题及其资料依据。"""

    id: int
    session_id: int
    question: str
    question_type: str
    question_index: int
    context: str | None = None

    # 资料依据。§9.3 要求资料型问题可追溯依据，
    # 因此接口必须把证据暴露出去，而不是只存在库里。
    evidence_chunk_ids: list[int] = Field(default_factory=list)
    is_general: bool = False

    created_at: datetime

    # 该题已有的回答（未作答时为 None）。
    # 与问题同层返回，避免调用方为每道题再发一次请求。
    answer: str | None = None
    answered_at: datetime | None = None

    model_config = ConfigDict(from_attributes=True)

    @property
    def has_evidence(self) -> bool:
        """是否有资料依据。

        便于前端区分"资料驱动的题目"与"通用能力题"，
        而不用去猜 evidence_chunk_ids 是否为空。
        """

        return bool(self.evidence_chunk_ids)


class InterviewEvidenceItem(BaseModel):
    """一条资料依据（面试问题所依据的资料片段）。

    存在的理由：§9.3 要求资料型问题**可追溯依据**。
    此前接口只暴露 `evidence_chunk_ids`（一串数字），
    用户看不到"这道题到底是从我哪段资料里来的" ——
    数字本身无法建立信任。
    """

    chunk_id: int

    # 片段正文。过长时由服务端截断（见端点说明）：
    # 前端是用来展示依据的，不需要整块 500 字。
    content: str

    # 是否被服务端截断过，让前端能如实提示"还有更多"。
    truncated: bool = False

    # 来源文件。用户据此判断"这是我的哪份资料"。
    document_id: int | None = None
    document_name: str | None = None
    document_type: str | None = None


class InterviewEvidenceResponse(BaseModel):
    """一道题的全部资料依据。"""

    question_id: int
    question: str
    is_general: bool = False

    # 按 `evidence_chunk_ids` 的**原顺序**返回。
    # 顺序反映了检索结果喂给模型时的位置，重排会让"依据"
    # 与"实际使用情况"对不上（见 context_builder 的说明）。
    items: list[InterviewEvidenceItem] = Field(default_factory=list)

    # 有依据但编号已失效（资料被删除）的 chunk 数。
    # 明确报出来而不是静默少几条 —— 否则用户会以为
    # 那道题本来就没依据。
    missing_chunk_ids: list[int] = Field(default_factory=list)


class InterviewAnswerResponse(BaseModel):
    """作答后返回的结果，含生成的追问。"""

    answer_id: int
    question_id: int
    answer: str
    answered_at: datetime

    # 本轮生成的追问。追问本身也是一个 InterviewQuestion，
    # 因此复用同一响应模型，前端无需区分两种问题结构。
    #
    # 为 None 表示**面试已结束**（题目预算耗尽），
    # 此时 `finished=True` 且 `evaluation` 有值。
    follow_up_question: InterviewQuestionResponse | None = None

    # 作答后的会话状态。让前端不必再查一次即可知道下一步。
    session_status: str

    # 是否因预算耗尽而自动结束了面试。
    #
    # 前端据 `finished` 决定显示"下一题"还是"查看报告"，
    # 不需要自己去比对题目数量。
    finished: bool = False

    termination_reason: str | None = None

    # 自动结束时的整场评价（未结束时为 None）。
    evaluation: dict | None = None

    # 本轮回答分析的摘要信息。
    # 只暴露状态与分数档位，不暴露完整分析——
    # 面试过程中把"技术深度偏低"这类判断直接展示给候选人，
    # 会干扰后续作答（他还没答完）。
    analysis_summary: dict | None = None

    model_config = ConfigDict(from_attributes=True)


class InterviewStartRequest(BaseModel):
    """开始面试的请求（可选）。

    不传 query 时，服务端依次退化为目标岗位、面试类型 ——
    让检索 query 至少有语义，而不是空字符串。
    """

    query: str | None = Field(
        default=None,
        description="面试方向，用于检索候选人资料",
    )

    question_type: str = Field(
        default="technical",
        description="首题类型",
    )


class InterviewStartResponse(BaseModel):
    """开始面试的结果。"""

    session_status: str
    current_question_index: int
    question: "InterviewQuestionResponse"


class InterviewStatusHistoryResponse(BaseModel):
    """一次状态转移的轨迹记录。"""

    id: int

    # 转移前后的状态。
    #
    # 原样返回库里的字符串，不做规范化 ——
    # 轨迹是审计记录，"当时库里是什么"必须可考
    # （历史数据里存在 "created" 这种旧值）。
    from_status: str
    to_status: str

    # 触发原因（取值见 TransitionTrigger）。
    trigger: str

    created_at: datetime

    model_config = ConfigDict(from_attributes=True)


class InterviewDetailResponse(BaseModel):
    """整场面试的完整可读状态：会话 + 全部问答 + 评价。"""

    session: dict
    questions: list[InterviewQuestionResponse] = Field(
        default_factory=list
    )

    # 已完成的评价（未评价时为 None）。
    evaluation: dict | None = None

    # 当前允许的状态转移，供前端决定显示哪些按钮。
    allowed_transitions: list[str] = Field(default_factory=list)
