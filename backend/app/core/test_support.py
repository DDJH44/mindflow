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


async def wait_for_document_indexed(
    client,
    project_id: int,
    document_id: int,
    timeout: float = 180.0,
) -> str | None:
    """等待文档的索引完成，返回最终状态。

    **为什么需要它**：上传自 §28 起是**异步**的 —— 接口返回 202
    时状态是 `chunked`，嵌入由后台 worker 完成。
    任何依赖"上传后即可检索"的验证脚本都必须先等这一步，
    否则它会以"检索命中 0 段"失败，而**失败信息指向的是
    错误的地方**（看起来像检索坏了，实际只是还没索引完）。

    放在 `test_support` 而不是各脚本自己写：三处复制必然漂移，
    而其中一处写错就会变成一次假的失败。
    """

    import asyncio

    elapsed = 0.0

    # 两种路径都试：有的脚本 `base_url` 已含 `/api`、用短路径，
    # 有的用完整 `/api/...`。写死一种会让另一半脚本静默拿到 404
    # 并最终以"索引超时"失败 —— 而真因是路径写错了。
    full_path = f"/api/projects/{project_id}/documents"
    short_path = f"/projects/{project_id}/documents"

    path = full_path

    while elapsed < timeout:
        response = await client.get(path)

        if response.status_code == 404 and path == full_path:
            path = short_path
            response = await client.get(path)

        documents = response.json()
        found = [
            item for item in documents if item["id"] == document_id
        ]

        if found and found[0]["status"] in ("embedded", "failed"):
            return found[0]["status"]

        await asyncio.sleep(1)
        elapsed += 1

    return None
