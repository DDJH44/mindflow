from fastapi import (
    APIRouter,
    Depends,
    HTTPException,
    Query,
    status,
)
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.dependencies import get_current_user
from app.database.session import get_db
from app.models.user import User
from app.repositories.document_chunk_repository import (
    DocumentChunkRepository,
)
from app.repositories.document_repository import DocumentRepository
from app.repositories.interview_question_repository import (
    InterviewQuestionRepository,
)
from app.repositories.interview_session_repository import (
    InterviewSessionRepository,
)
from app.repositories.project_repository import ProjectRepository
from app.schemas.interview.ability_profile import (
    AbilityProfileResponse,
)
from app.schemas.interview.interview_qa import (
    InterviewAnswerCreate,
    InterviewAnswerResponse,
    InterviewDetailResponse,
    InterviewEvidenceResponse,
    InterviewQuestionResponse,
    InterviewStartRequest,
    InterviewStartResponse,
    InterviewStatusHistoryResponse,
)
from app.schemas.interview.interview_session import (
    InterviewSessionCreate,
    InterviewSessionListItem,
    InterviewSessionListResponse,
    InterviewSessionResponse,
    InterviewSessionTransition,
)
from app.services.interview.ability_profile_service import (
    AbilityProfileService,
)
from app.services.interview.interview_flow_service import (
    InterviewFlowService,
)
from app.services.interview.interview_state_machine import (
    InvalidTransitionError,
)
from app.services.interview.interview_turn_service import (
    InterviewTurnService,
)
from app.services.interview_session_service import InterviewSessionService
from app.services.usage.usage_quota import (
    QuotaExceeded,
    UsageMetric,
)
from app.services.usage.usage_service import UsageService


router = APIRouter(
    prefix="/api/interviews",
    tags=["Interviews"],
)


async def _load_owned_session(
    interview_id: int,
    db: AsyncSession,
    current_user: User,
):
    """取会话并校验它属于当前用户。

    同时检查两个来源（§5.4 / ADR-030）：
    - `session.user_id`：会话创建者。历史面试记录归属于**当时**的
      使用者，不随项目易主。
    - `project.owner_id`：项目当前所有者。

    为什么两个都要查：
    只信 `user_id` 会漏掉"项目已易主但仍能访问旧会话"；
    只信 `project.owner_id` 会漏掉"会话创建者已无权访问"。
    两者都通过才算有权，宁可严一点 ——
    面试记录里包含候选人简历的推导内容，越权代价高。
    """

    interview_service = InterviewSessionService(db)

    interview_session = await interview_service.get_session(
        interview_id=interview_id,
    )

    if not interview_session:
        return None

    if interview_session.user_id != current_user.id:
        return None

    project_repository = ProjectRepository(db)

    project = await project_repository.get_by_id_and_owner(
        project_id=interview_session.project_id,
        owner_id=current_user.id,
    )

    if not project:
        return None

    return interview_session


def _session_payload(interview_session) -> dict:
    """会话的对外表示。

    显式构造而不是直接返回 ORM 对象：
    `InterviewSessionResponse` 用 from_attributes 也能工作，
    但内联构造可以确保**暂停字段不会意外外泄**，
    也便于将来增删字段时对照。
    """

    return {
        "id": interview_session.id,
        "user_id": interview_session.user_id,
        "project_id": interview_session.project_id,
        "status": interview_session.status,
        "interview_type": interview_session.interview_type,
        "target_role": interview_session.target_role,
        "current_question_index": (
            interview_session.current_question_index
        ),
        # 题目预算与终止信息必须暴露：
        # 前端据此显示"第 3 题 / 共 8 题"，以及区分
        # "额度用完"与"用户自己关了"。
        "max_questions": interview_session.max_questions,
        "questions_asked": interview_session.questions_asked,
        "termination_reason": (
            interview_session.termination_reason
        ),
        "resume_status": interview_session.resume_status,
        "pause_reason": interview_session.pause_reason,
        "paused_at": interview_session.paused_at,
        "created_at": interview_session.created_at,
        "updated_at": interview_session.updated_at,
    }


def _bad_transition(exc: Exception) -> HTTPException:
    """非法转移统一映射为 409。

    不静默忽略：调用方必须知道状态没变（ADR-010）。
    """

    return HTTPException(
        status_code=status.HTTP_409_CONFLICT,
        detail=str(exc),
    )


def _llm_error_or_none(exc: Exception) -> HTTPException | None:
    """把上游 LLM 的失败映射成 503，并给出可读原因。

    为什么需要（D57）：LLM SDK 的超时异常继承自
    `openai.OpenAIError`，**既不是 `ValueError` 也不是 `RuntimeError`**，
    而生成题目 / 分析回答 / 整场评价三处只处理了后两者 ——
    于是上游一慢就变成裸 500 "Internal Server Error"，
    客户端拿不到任何提示，运维也看不出是上游问题还是代码 bug。

    判据用**类名与 MRO 特征**而不是 `import openai`：
    本项目允许替换 LLM 实现（§24 换过端点），
    硬绑具体 SDK 的异常类会让替换后这个映射失效。
    """

    names = {cls.__name__ for cls in type(exc).__mro__}

    is_llm_error = bool(
        names
        & {
            "OpenAIError",
            "APITimeoutError",
            "APIConnectionError",
            "APIStatusError",
            "RateLimitError",
        }
    )

    if not is_llm_error:
        return None

    detail = str(exc) or type(exc).__name__

    return HTTPException(
        status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
        detail=(
            f"调用模型失败（{type(exc).__name__}）：{detail[:200]}。"
            "这是上游模型服务的临时问题，稍后重试即可；"
            "本次操作没有写入任何数据。"
        ),
    )


# ============================================================
# 能力画像（Phase 6）
# ============================================================


@router.get(
    "/profile/ability",
    response_model=AbilityProfileResponse,
)
async def get_ability_profile(
    project_id: int | None = Query(
        default=None,
        description="只看某个项目下的面试（不传则跨全部项目）",
    ),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """当前用户的能力画像。

    路径放在 `/profile/ability` 而不是 `/profile/{user_id}/ability`：
    画像永远只看自己，让调用方传 user_id 等于给越权留一个口子。
    """

    # 项目名：画像要显示"这是哪个项目的面试"，
    # 按 id 批量取名，避免在评价仓储里再写一遍 join。
    project_repository = ProjectRepository(db)
    projects = await project_repository.get_by_owner_id(
        owner_id=current_user.id
    )
    project_names = {
        project.id: project.name for project in projects
    }

    profile = await AbilityProfileService(db).build(
        user_id=current_user.id,
        project_id=project_id,
        project_names=project_names,
    )

    return {
        "session_count": profile.session_count,
        "dimensions": [
            {
                "key": dimension.key,
                "label": dimension.label,
                "count": dimension.count,
                "median": dimension.median,
                "minimum": dimension.minimum,
                "maximum": dimension.maximum,
                "latest": dimension.latest,
                "spread": dimension.spread,
                "history": dimension.history,
            }
            for dimension in profile.dimensions
        ],
        "sessions": [
            {
                "session_id": item.session_id,
                "project_id": item.project_id,
                "project_name": item.project_name,
                "target_role": item.target_role,
                "evaluated_at": item.evaluated_at,
                "scores": item.scores,
            }
            for item in profile.sessions
        ],
        "recent_weaknesses": profile.recent_weaknesses,
        "recent_suggestions": profile.recent_suggestions,
        "recurring_weaknesses": [
            {
                "text": item.text,
                "occurrences": item.occurrences,
            }
            for item in profile.recurring_weaknesses
        ],
        "sufficient_samples": profile.sufficient_samples,
        "caveats": profile.caveats,
    }


# ============================================================
# 会话
# ============================================================

# 这些状态下的会话**还没结束**，用户可以回去继续。
#
# 为什么在服务端定义：前端的"进行中"筛选若自己列一遍状态，
# 状态机一旦新增状态（例如将来加 `reviewing`）前端就会漏掉，
# 表现出"面试不见了"。由服务端给出唯一口径。
#
# 覆盖状态机的全部非终态，另加迁移前的旧值 `created`
# （`LEGACY_STATUS_MAP` 会把它归一成 draft，但库里的字面值还在）。
UNFINISHED_STATUSES = (
    "created",
    "draft",
    "preparing_context",
    "planned",
    "asking",
    "waiting_for_answer",
    "evaluating",
    "summarizing",
    "paused",
)


@router.get(
    "",
    response_model=InterviewSessionListResponse,
)
async def list_interview_sessions(
    unfinished_only: bool = Query(
        default=False,
        description=(
            "只返回尚未结束的会话。用于「找回没做完的面试」"
        ),
    ),
    project_id: int | None = Query(
        default=None,
        description="只看某个项目下的面试",
    ),
    limit: int = Query(default=20, ge=1, le=50),
    offset: int = Query(default=0, ge=0),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """
    列出当前用户的面试会话，最近更新的在前。

    没有这个端点时，用户关掉页面就**找不回进行中的面试** ——
    那场面试既占用了额度，也无法继续，等于白做。
    """

    repository = InterviewSessionRepository(db)

    statuses = list(UNFINISHED_STATUSES) if unfinished_only else None

    sessions = await repository.get_by_user(
        user_id=current_user.id,
        statuses=statuses,
        project_id=project_id,
        limit=limit,
        offset=offset,
    )

    total = await repository.count_by_user(
        user_id=current_user.id,
        statuses=statuses,
        project_id=project_id,
    )

    # 项目名：按 id 批量取，避免在会话仓储里再写一遍 join
    # （项目的读取口径收在 ProjectRepository）。
    project_repository = ProjectRepository(db)
    projects = await project_repository.get_by_owner_id(
        owner_id=current_user.id
    )
    project_names = {
        project.id: project.name for project in projects
    }

    # 已作答数：`questions_asked` 是"问了几题"，不等于"答了几题"。
    answered_counts = await repository.get_answered_counts(
        session_ids=[session.id for session in sessions]
    )

    return {
        "items": [
            {
                **_session_payload(session),
                "project_name": project_names.get(
                    session.project_id
                ),
                "answered_count": answered_counts.get(
                    session.id, 0
                ),
            }
            for session in sessions
        ],
        "total": total,
        "limit": limit,
        "offset": offset,
    }


@router.post(
    "",
    response_model=InterviewSessionResponse,
    status_code=status.HTTP_201_CREATED,
)
async def create_interview_session(
    data: InterviewSessionCreate,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """
    创建面试会话。

    初始状态为 draft（状态机起点）。
    """

    project_repository = ProjectRepository(db)

    project = await project_repository.get_by_id_and_owner(
        project_id=data.project_id,
        owner_id=current_user.id,
    )

    if not project:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="项目不存在",
        )

    # 注意：这里**不**消费配额。
    #
    # 创建会话只是建了一个 draft 草稿，还没有产生任何 LLM 调用。
    # 若在创建时扣额度，用户点了"新建面试"又没开始就损失一场 ——
    # 额度应当只在**真正开始面试时**消费（见 start_interview）。
    interview_service = InterviewSessionService(db)

    try:
        interview_session = await interview_service.create_session(
            project_id=data.project_id,
            user_id=current_user.id,
            interview_type=data.interview_type,
            target_role=data.target_role,
            max_questions=data.max_questions,
        )
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(exc),
        ) from exc

    return interview_session


@router.get(
    "/{interview_id}",
    response_model=InterviewSessionResponse,
)
async def get_interview_session(
    interview_id: int,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """
    获取面试会话详情。
    """

    interview_session = await _load_owned_session(
        interview_id=interview_id,
        db=db,
        current_user=current_user,
    )

    if not interview_session:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="面试会话不存在",
        )

    return interview_session


@router.get(
    "/{interview_id}/detail",
    response_model=InterviewDetailResponse,
)
async def get_interview_detail(
    interview_id: int,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """
    获取整场面试的完整可读状态：会话 + 全部问答 + 评价。

    一次请求拿到全部内容，避免前端为每道题各发一次请求。
    ``allowed_transitions`` 让前端不必自己实现状态机规则。
    """

    interview_session = await _load_owned_session(
        interview_id=interview_id,
        db=db,
        current_user=current_user,
    )

    if not interview_session:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="面试会话不存在",
        )

    flow = InterviewFlowService(db)
    session_service = InterviewSessionService(db)

    questions = await flow.get_questions_with_answers(
        session_id=interview_id
    )

    return {
        "session": _session_payload(interview_session),
        "questions": questions,
        "evaluation": await flow.get_latest_evaluation(
            session_id=interview_id
        ),
        "allowed_transitions": await session_service.allowed_next_status(
            interview_session
        ),
    }


@router.get(
    "/{interview_id}/allowed-transitions",
    response_model=list[str],
)
async def get_allowed_transitions(
    interview_id: int,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """
    查询当前会话可以转移到哪些状态。

    对 paused 会话会结合 resume_status 动态计算，
    避免前后端各写一份状态机规则、逐渐不一致。
    """

    interview_session = await _load_owned_session(
        interview_id=interview_id,
        db=db,
        current_user=current_user,
    )

    if not interview_session:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="面试会话不存在",
        )

    return await InterviewSessionService(db).allowed_next_status(
        interview_session
    )


@router.post(
    "/{interview_id}/transition",
    response_model=InterviewSessionResponse,
)
async def transition_interview_session(
    interview_id: int,
    data: InterviewSessionTransition,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """
    把会话转移到目标状态。

    非法转移返回 409 而不是静默忽略：
    状态机要保证"可恢复、可审计"（ADR-010），
    静默失败会让调用方以为状态已改，产生难以排查的不一致。
    """

    interview_session = await _load_owned_session(
        interview_id=interview_id,
        db=db,
        current_user=current_user,
    )

    if not interview_session:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="面试会话不存在",
        )

    interview_service = InterviewSessionService(db)

    try:
        updated = await interview_service.transition_status(
            interview_id=interview_id,
            target_status=data.target_status,
        )
    except InvalidTransitionError as exc:
        raise _bad_transition(exc) from exc

    return updated


# ============================================================
# 状态轨迹
# ============================================================


@router.get(
    "/{interview_id}/history",
    response_model=list[InterviewStatusHistoryResponse],
)
async def get_interview_history(
    interview_id: int,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """
    查询会话的状态转移轨迹（正序）。

    会话表只保存当前状态，轨迹是唯一能回答
    "什么时候变成这样、由什么引起"的依据。
    """

    interview_session = await _load_owned_session(
        interview_id=interview_id,
        db=db,
        current_user=current_user,
    )

    if not interview_session:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="面试会话不存在",
        )

    return await InterviewFlowService(db).get_status_history(
        session_id=interview_id
    )


# ============================================================
# 开始面试 / 生成题目
# ============================================================


@router.post(
    "/{interview_id}/start",
    response_model=InterviewStartResponse,
)
async def start_interview(
    interview_id: int,
    data: InterviewStartRequest | None = None,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """
    开始面试：准备上下文 → 生成首题 → 进入 asking。

    对调用方是一个动作，而不是三个状态转移 ——
    暴露三个转移会让前端必须自己实现状态机规则，
    并在中途失败时留下半开状态（停在 planned 但没题目）。

    会话处于 paused（且暂停在开始前的状态）时会先自动恢复。
    生成问题会调用 LLM 与向量检索，耗时较长。
    """

    interview_session = await _load_owned_session(
        interview_id=interview_id,
        db=db,
        current_user=current_user,
    )

    if not interview_session:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="面试会话不存在",
        )

    # 配额在这里消费，而不是在创建会话时。
    #
    # 理由是"什么时候真正开始花钱"：创建 draft 草稿不产生任何
    # LLM 调用，开始面试才会（检索 + 生成首题）。
    # 在创建时扣额度会让"点了新建又没开始"白损失一场。
    #
    # 先自增再判定（见 UsageService）：先判定后自增
    # 会让并发请求一起通过校验。
    usage_service = UsageService(db)

    try:
        await usage_service.consume(
            user_id=current_user.id,
            metric=UsageMetric.INTERVIEW_STARTED,
            quota=current_user.interview_quota,
        )
    except QuotaExceeded as exc:
        # 配额不足要回滚这次自增：否则"额度用完但还在涨"，
        # 用户看到的剩余额度会变成负数。
        await db.rollback()
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail=str(exc),
        ) from exc

    try:
        result = await InterviewFlowService(db).start_interview(
            session_id=interview_id,
            query=data.query if data else None,
            question_type=(
                data.question_type if data else "technical"
            ),
        )
    except QuotaExceeded as exc:
        # 题目额度不足。此时**业务数据尚未提交**
        # （生成题目在扣额度之后才落库），因此可以安全回滚，
        # 不会留下"题目已存但额度没扣"的不一致。
        await db.rollback()
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail=str(exc),
        ) from exc
    except ValueError as exc:
        # 开始失败（状态不允许、项目异常）也要回滚配额，
        # 否则一次失败会持续吃掉额度。
        await db.rollback()
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=str(exc),
        ) from exc
    except Exception as exc:
        # 上游 LLM 超时/连接失败走这里（D57）：
        # 它既不是 ValueError 也不是 RuntimeError，不接住就会变成
        # 裸 500，用户看到 "Internal Server Error" 而不知是上游问题。
        mapped = _llm_error_or_none(exc)

        if mapped is None:
            # 不是 LLM 问题：原样抛出，保留堆栈供排查
            raise

        # 模型没返回，题目没生成 —— 回滚这次配额消费，
        # 否则用户为了一个"上游抖动"白扣一场额度。
        await db.rollback()
        raise mapped from exc

    question = result["question"]
    refreshed = result["session"]

    return {
        "session_status": refreshed.status,
        "current_question_index": refreshed.current_question_index,
        "question": {
            "id": question.id,
            "session_id": question.session_id,
            "question": question.question,
            "question_type": question.question_type,
            "question_index": question.question_index,
            "context": question.context,
            "evidence_chunk_ids": list(
                question.evidence_chunk_ids or []
            ),
            "is_general": question.is_general,
            "created_at": question.created_at,
            "answer": None,
            "answered_at": None,
        },
    }


# ============================================================
# 暂停 / 恢复
# ============================================================


@router.post(
    "/{interview_id}/pause",
    response_model=InterviewSessionResponse,
)
async def pause_interview_session(
    interview_id: int,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """
    暂停会话，并记住当前状态以便恢复。

    用专门的端点而不是通用 transition：
    暂停必须同时写入 resume_status，
    否则会话会变成只能取消的死胡同。
    """

    interview_session = await _load_owned_session(
        interview_id=interview_id,
        db=db,
        current_user=current_user,
    )

    if not interview_session:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="面试会话不存在",
        )

    try:
        return await InterviewSessionService(db).pause_session(
            interview_id=interview_id,
            reason="用户主动暂停",
        )
    except ValueError as exc:
        raise _bad_transition(exc) from exc


@router.post(
    "/{interview_id}/resume",
    response_model=InterviewSessionResponse,
)
async def resume_interview_session(
    interview_id: int,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """
    从 paused 恢复到暂停前的状态。
    """

    interview_session = await _load_owned_session(
        interview_id=interview_id,
        db=db,
        current_user=current_user,
    )

    if not interview_session:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="面试会话不存在",
        )

    try:
        return await InterviewSessionService(db).resume_session(
            interview_id=interview_id,
        )
    except ValueError as exc:
        raise _bad_transition(exc) from exc


# ============================================================
# 答题
# ============================================================


@router.post(
    "/{interview_id}/answer",
    response_model=InterviewAnswerResponse,
)
async def submit_answer(
    interview_id: int,
    data: InterviewAnswerCreate,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """
    提交一道题的作答，并返回本轮生成的追问。

    会调用 LLM 做回答分析，因此耗时较长（线上单次采样约数秒）。
    """

    interview_session = await _load_owned_session(
        interview_id=interview_id,
        db=db,
        current_user=current_user,
    )

    if not interview_session:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="面试会话不存在",
        )

    # 必须校验问题属于该会话。
    # 只信任请求体里的 question_id 会让用户能对别人的题目作答 ——
    # 所有权校验放在会话上，但题目与会话的从属关系必须在此确认。
    flow = InterviewFlowService(db)
    questions = await flow.get_questions_with_answers(
        session_id=interview_id
    )
    known_ids = {item["id"] for item in questions}

    if data.question_id not in known_ids:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="该问题不属于此面试会话",
        )

    try:
        result = await InterviewTurnService(db).process_answer(
            question_id=data.question_id,
            answer=data.answer,
        )
    except QuotaExceeded as exc:
        # 回答已落库、分析已完成，但追问生成时发现月度题目额度用尽。
        # 此时**不能**回滚：回答与用量都已经提交，
        # 回滚会丢掉用户已经产生的数据。直接返回 429。
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail=str(exc),
        ) from exc
    except InvalidTransitionError as exc:
        raise _bad_transition(exc) from exc
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(exc),
        ) from exc
    except Exception as exc:
        # 上游 LLM 超时/连接失败（D57）。
        #
        # 这里**不回滚**：回答与分析可能已经在同一事务里提交，
        # 回滚会丢掉用户刚提交的作答 —— 那比"少一条追问"更糟。
        # 因此只把错误讲清楚，让用户知道作答已保存、可稍后继续。
        mapped = _llm_error_or_none(exc)

        if mapped is None:
            raise

        mapped.detail = (
            f"{mapped.detail}本次作答可能已保存，"
            "刷新页面查看当前进度。"
        )
        raise mapped from exc

    analysis = result["analysis"]
    follow_up = result["follow_up_question"]

    # 预算耗尽时 follow_up 为 None，且带回了 evaluation。
    evaluation = result.get("evaluation")

    return {
        "answer_id": result["answer"].id,
        "question_id": data.question_id,
        "answer": result["answer"].answer,
        "answered_at": result["answer"].created_at,
        "follow_up_question": (
            {
                "id": follow_up.id,
                "session_id": follow_up.session_id,
                "question": follow_up.question,
                "question_type": follow_up.question_type,
                "question_index": follow_up.question_index,
                "context": follow_up.context,
                "evidence_chunk_ids": list(
                    follow_up.evidence_chunk_ids or []
                ),
                "is_general": follow_up.is_general,
                "created_at": follow_up.created_at,
                "answer": None,
                "answered_at": None,
            }
            if follow_up is not None
            else None
        ),
        "session_status": result["session_status"],
        "finished": result.get("finished", False),
        "termination_reason": result.get("termination_reason"),
        "evaluation": (
            {
                "id": evaluation.id,
                "overall_score": evaluation.overall_score,
                "technical_score": evaluation.technical_score,
                "project_score": evaluation.project_score,
                "communication_score": (
                    evaluation.communication_score
                ),
                "strengths": list(evaluation.strengths or []),
                "weaknesses": list(evaluation.weaknesses or []),
                "suggestions": list(evaluation.suggestions or []),
                "feedback": evaluation.feedback,
            }
            if evaluation is not None
            else None
        ),
        # 只回档位与缺口数量，不回完整分析。
        # 面试过程中把"技术深度偏低"直接展示给候选人，
        # 会干扰后续作答 —— 他还没答完。
        "analysis_summary": {
            "sample_count": analysis.sample_count,
            "missing_points_count": len(analysis.missing_points),
            "anchors": {
                key: value.get("anchor")
                for key, value in (
                    analysis.anchor_summary().items()
                )
                if isinstance(value, dict)
            },
        },
    }


@router.get(
    "/{interview_id}/questions/{question_id}/evidence",
    response_model=InterviewEvidenceResponse,
)
async def get_question_evidence(
    interview_id: int,
    question_id: int,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """取一道题的资料依据（片段正文）。

    为什么路径里要带 `question_id`，而不是直接收一串 chunk id：
    直接收 id 会让任何登录用户都能读**任意项目的**资料片段
    （IDOR）—— chunk 自带 `project_id`，但没有任何依据能证明
    调用方有权看它。从问题派生就天然受限：问题属于会话，
    会话已经过所有权校验。

    片段内容按需截断：前端只是展示"这道题的依据是哪几段"，
    返回整块 500 字没有意义，还会让响应体无谓变大。
    """

    interview_session = await _load_owned_session(
        interview_id=interview_id,
        db=db,
        current_user=current_user,
    )

    if not interview_session:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="面试会话不存在",
        )

    question_repository = InterviewQuestionRepository(db)

    question = await question_repository.get_by_id(question_id)

    if not question or question.session_id != interview_id:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="该问题不属于此面试会话",
        )

    chunk_ids = [
        int(chunk_id)
        for chunk_id in (question.evidence_chunk_ids or [])
    ]

    chunk_repository = DocumentChunkRepository(db)

    chunks = await chunk_repository.get_by_ids(chunk_ids)

    # 资料名：按 chunk 里的 document_id 一次性取回，避免逐条查询
    document_ids = [
        chunk.document_id
        for chunk in chunks.values()
        if chunk.document_id is not None
    ]

    document_repository = DocumentRepository(db)

    documents = await document_repository.get_by_ids(document_ids)

    items = []
    missing = []

    for chunk_id in chunk_ids:
        chunk = chunks.get(chunk_id)

        if chunk is None:
            # 资料被删除后依据会失效。明确报出来，让前端能提示
            # "这条依据引用的资料已被删除"，而不是静默少一条。
            missing.append(chunk_id)
            continue

        document = documents.get(chunk.document_id)

        # 返回**完整**片段，不截断。
        #
        # 切块上限本来就是 500 字，返回完整片段对响应体没有实质影响；
        # 而截断会隐藏信息 —— 用户看到的应该就是系统当时看到的那一段，
        # 那正是"依据"要传达的东西。
        items.append(
            {
                "chunk_id": chunk.id,
                "content": chunk.content or "",
                "document_id": chunk.document_id,
                "document_name": (
                    document.name if document else None
                ),
                "document_type": (
                    document.document_type if document else None
                ),
            }
        )

    return {
        "question_id": question.id,
        "question": question.question,
        "is_general": question.is_general,
        "items": items,
        "missing_chunk_ids": missing,
    }


@router.get(
    "/{interview_id}/questions",
    response_model=list[InterviewQuestionResponse],
)
async def list_interview_questions(
    interview_id: int,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """
    列出面试的全部题目及各自的回答。
    """

    interview_session = await _load_owned_session(
        interview_id=interview_id,
        db=db,
        current_user=current_user,
    )

    if not interview_session:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="面试会话不存在",
        )

    return await InterviewFlowService(
        db
    ).get_questions_with_answers(session_id=interview_id)


# ============================================================
# 结束
# ============================================================


@router.post("/{interview_id}/finish")
async def finish_interview(
    interview_id: int,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """
    结束面试并生成整场评价。

    评价会调用 LLM，耗时较长。成功后会话进入 completed（终态）。
    评价失败时会话停在 evaluating —— 该状态可**直接重试本端点**。
    """

    interview_session = await _load_owned_session(
        interview_id=interview_id,
        db=db,
        current_user=current_user,
    )

    if not interview_session:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="面试会话不存在",
        )

    try:
        result = await InterviewFlowService(db).finish_interview(
            session_id=interview_id
        )
    except InvalidTransitionError as exc:
        raise _bad_transition(exc) from exc
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(exc),
        ) from exc
    except Exception as exc:
        # 整场评价要调用 LLM（D57）：模型侧超时/连接失败时给出 503
        # 与可读原因，而不是裸 500。
        #
        # 不回滚：会话已推进到 `evaluating`，该状态**可以直接重试**
        # 本端点（ADR-025），回滚状态反而会让重试路径变复杂。
        mapped = _llm_error_or_none(exc)

        if mapped is None:
            raise

        raise mapped from exc

    saved = result["saved_evaluation"]
    refreshed = await InterviewSessionService(db).get_session(
        interview_id
    )

    return {
        "session_status": refreshed.status,
        "evaluation": {
            "id": saved.id,
            "overall_score": saved.overall_score,
            "technical_score": saved.technical_score,
            "project_score": saved.project_score,
            "communication_score": saved.communication_score,
            "strengths": list(saved.strengths or []),
            "weaknesses": list(saved.weaknesses or []),
            "suggestions": list(saved.suggestions or []),
            "feedback": saved.feedback,
        },
    }
