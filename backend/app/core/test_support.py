"""验证脚本的共享脚手架。

存在的理由：需要写库的验证脚本若依赖"数据库恰好是什么样"，
会互相干扰 —— 实测中配额被前几个脚本累计消耗，
导致后面的脚本以 429 失败，而失败信息指向的是**错误的地方**
（看起来像"开始面试坏了"，实际是额度用完了）。

因此约定：**凡依赖特定库状态的脚本，自己 prepare、自己 restore。**
"""

from sqlalchemy import text

from app.database.session import AsyncSessionLocal
from app.services.usage.usage_quota import (
    DEFAULT_INTERVIEW_QUOTA,
    DEFAULT_QUESTION_QUOTA,
    UsageMetric,
)


async def grant_quota(
    user_id: int,
    quota: int = DEFAULT_INTERVIEW_QUOTA,
    question_quota: int = DEFAULT_QUESTION_QUOTA,
) -> None:
    """清空本账期用量并把**两份**额度设为指定值。

    必须同时设置题目额度：它默认 100，
    而只设场次额度会让"题目额度先耗尽"悄悄挡住测试。

    这是**测试后门**：直接写库而不是走接口 ——
    当前没有也不该有"修改自己配额"的接口
    （用户能自己改额度就等于没有配额）。
    """

    async with AsyncSessionLocal() as db:
        await db.execute(
            text("DELETE FROM interview_usage WHERE user_id = :uid"),
            {"uid": user_id},
        )
        await db.execute(
            text(
                "UPDATE users SET interview_quota = :q, "
                "monthly_question_quota = :qq WHERE id = :uid"
            ),
            {"q": quota, "qq": question_quota, "uid": user_id},
        )
        await db.commit()


async def reset_quota(user_id: int) -> None:
    """清空用量并恢复两份额度的默认值。"""

    await grant_quota(
        user_id,
        DEFAULT_INTERVIEW_QUOTA,
        DEFAULT_QUESTION_QUOTA,
    )


async def get_used(
    user_id: int,
    metric: UsageMetric = UsageMetric.INTERVIEW_STARTED,
) -> int:
    """读取本账期已用量，供断言使用。"""

    from app.services.usage.usage_quota import current_period

    async with AsyncSessionLocal() as db:
        value = (
            await db.execute(
                text(
                    "SELECT count FROM interview_usage "
                    "WHERE user_id = :uid AND period = :p "
                    "AND metric = :m"
                ),
                {
                    "uid": user_id,
                    "p": current_period(),
                    "m": metric.value,
                },
            )
        ).scalar_one_or_none()

    return int(value) if value is not None else 0


async def delete_sessions(session_ids: list[int | None]) -> None:
    """删除测试会话（轨迹、问答、评价随之级联删除）。"""

    async with AsyncSessionLocal() as db:
        for sid in session_ids:
            if sid is None:
                continue
            await db.execute(
                text("DELETE FROM interview_sessions WHERE id = :sid"),
                {"sid": sid},
            )
        await db.commit()
