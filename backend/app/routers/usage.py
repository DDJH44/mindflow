from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.dependencies import get_current_user
from app.database.session import get_db
from app.models.user import User
from app.schemas.usage import UsageResponse
from app.services.interview.interview_termination import (
    DEFAULT_MAX_QUESTIONS,
)
from app.services.usage.interview_cost import estimate_cost
from app.services.usage.usage_quota import UsageMetric
from app.services.usage.usage_service import UsageService


router = APIRouter(
    prefix="/api/usage",
    tags=["Usage"],
)


@router.get(
    "",
    response_model=UsageResponse,
)
async def get_current_usage(
    # 用户改了"题目数量上限"后，前端带这个参数重新请求，
    # 就能拿到"这一场够不够开完"的判定。
    #
    # ⚠️ 默认值用服务端的 `DEFAULT_MAX_QUESTIONS`，**不用 8**：
    # 那个默认值只应存在于一处，否则调它时这里会不跟着变 ——
    # 与 D63/D70 是同一类错误（值被手抄到第二处）。
    max_questions: int | None = Query(
        default=None,
        description="按这个题目预算预估；省略时按服务端默认值",
    ),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """
    查询当前账期的两份额度：面试场次与题目总数，以及开一场的成本预估。

    前端据此在额度耗尽**之前**提示用户（例如"本月还剩 1 场"），
    而不是等他点了"开始面试"才收到 429。

    `limited_by` 指出当前受限方 —— 比较"哪个额度更紧"的规则
    只应存在于服务端，前端不该自己判定。

    `cost_preview` 回答**另一个问题**：不只是"能不能开始"，
    而是"**能不能开完**"。两者不同，而后者才会让用户白损失一场。
    """

    pair = await UsageService(db).get_quota_pair(
        user_id=current_user.id,
        interview_quota=current_user.interview_quota,
        question_quota=current_user.monthly_question_quota,
    )

    estimate = estimate_cost(
        max_questions=(
            max_questions
            if max_questions is not None
            else DEFAULT_MAX_QUESTIONS
        ),
        interviews_remaining=pair.interviews_remaining,
        questions_remaining=pair.questions_remaining,
    )

    return {
        "period": pair.period,
        "interviews": {
            "metric": UsageMetric.INTERVIEW_STARTED.value,
            "used": pair.interviews_used,
            "quota": pair.interview_quota,
            "remaining": pair.interviews_remaining,
        },
        "questions": {
            "metric": UsageMetric.QUESTION_GENERATED.value,
            "used": pair.questions_used,
            "quota": pair.question_quota,
            "remaining": pair.questions_remaining,
        },
        "limited_by": pair.limited_by,
        "allowed": pair.allowed,
        "cost_preview": {
            "questions": estimate.questions,
            "interviews": estimate.interviews,
            "fits": estimate.fits,
            "blocked_by": estimate.blocked_by,
            "truncates_at": estimate.truncates_at,
            "message": estimate.describe(pair.questions_remaining),
        },
    }
