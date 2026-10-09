from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.dependencies import get_current_user
from app.database.session import get_db
from app.models.user import User
from app.schemas.usage import UsageResponse
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
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """
    查询当前账期的两份额度：面试场次与题目总数。

    前端据此在额度耗尽**之前**提示用户（例如"本月还剩 1 场"），
    而不是等他点了"开始面试"才收到 429。

    `limited_by` 指出当前受限方 —— 比较"哪个额度更紧"的规则
    只应存在于服务端，前端不该自己判定。
    """

    pair = await UsageService(db).get_quota_pair(
        user_id=current_user.id,
        interview_quota=current_user.interview_quota,
        question_quota=current_user.monthly_question_quota,
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
    }
