from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field

from app.services.interview.interview_state_machine import (
    SessionStatus,
)


class InterviewSessionCreate(BaseModel):
    project_id: int
    interview_type: str = "technical"

    # 面试目标岗位。§5.4 规划字段，Interview Planner 会用到。
    target_role: str | None = Field(
        default=None,
        max_length=100,
        description="面试目标岗位，例如「后端工程师」",
    )

    # 题目预算上限（含追问）。
    #
    # 允许调用方指定，是因为"一场面试多长"是业务决策
    # （免费用户短、付费用户长）。服务端仍会把它夹取到
    # interview_termination 允许的范围内，防止误配置导致成本失控。
    max_questions: int | None = Field(
        default=None,
        ge=1,
        le=30,
        description=(
            "题目预算上限，含追问。不传则用服务端默认值（8）"
        ),
    )


class InterviewSessionResponse(BaseModel):
    id: int

    # 会话所有者。对调用方而言它必然等于当前登录用户
    # （所有权校验已经保证），暴露出来是为了让前端**不必再推导**
    # 会话归属，也便于排查"这场面试是谁的"。
    user_id: int

    project_id: int
    status: str
    interview_type: str
    target_role: str | None = None
    current_question_index: int

    # 题目预算与已问数量。
    # 前端据此显示"第 3 题 / 共 8 题"，而不必自己计数。
    max_questions: int
    questions_asked: int

    # 结束原因（取值见 interview_termination.TerminationReason）。
    # 业务侧要区分"额度用完"与"用户自己关了"，后续动作不同。
    termination_reason: str | None = None

    # 暂停信息。
    # 恢复目标必须暴露给前端：前端据此显示"继续面试"会回到哪一步，
    # 而不必自己推断状态机规则。
    resume_status: str | None = None
    pause_reason: str | None = None
    paused_at: datetime | None = None

    created_at: datetime
    updated_at: datetime

    model_config = ConfigDict(from_attributes=True)


class InterviewSessionTransition(BaseModel):
    """请求把会话转移到目标状态。

    合法转移由状态机校验（见 interview_state_machine）：
    非法转移返回 409，而不是静默忽略或强改状态。
    """

    target_status: SessionStatus = Field(
        description="目标状态，必须是状态机允许的合法取值",
    )


class InterviewSessionListItem(InterviewSessionResponse):
    """历史列表里的一条会话。

    继承会话响应并补两项**只有列表才需要**的信息，
    避免为了列表而在会话表上做连表。
    """

    # 项目名。
    #
    # 为什么不直接 join `projects`：项目的读取口径已经收在
    # `ProjectRepository`（含所有权校验），在会话仓储里再写一遍
    # join 会让"项目怎么读"出现第二种实现。
    # 列表最多 50 条、涉及的项目通常只有几个，因此按 id 批量取名。
    project_name: str | None = None

    # 该会话已作答的题数。
    #
    # `questions_asked` 是"问了几题"，不等于"答了几题"：
    # 用户可能看到题就关了。历史列表要显示"已作答 3 题"，
    # 用 questions_asked 会把没答的也算上。
    answered_count: int = 0


class InterviewSessionListResponse(BaseModel):
    """面试历史的分页结果。"""

    items: list[InterviewSessionListItem]

    # 总数用于"共 N 场"与页码计算。只给当页会让前端无法分页。
    total: int

    limit: int
    offset: int
