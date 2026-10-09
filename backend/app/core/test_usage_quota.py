"""用量计量与配额验证。

覆盖 §23.4 第 2 步要求的"业务压力"：用户能开始的面试场次有限额，
且限额是**服务端可强制**的。

两个刻意的设计：

1. **不调用 LLM。** 配额是纯业务逻辑，验证它不需要产生推理成本。
   因此本脚本比其它验证快得多。
2. **自己管配额。** 每次运行先清空本账期用量并把配额设成固定值，
   结束时恢复默认 —— 否则前一次运行的累计消耗会让它失败
   （这正是第一次写成"在创建会话时扣额度"时踩到的坑）。

依赖：PostgreSQL。不需要 Milvus / LLM。
运行：uv run python -m app.core.test_usage_quota
"""

import asyncio
import sys
from datetime import datetime, timezone

import httpx
from sqlalchemy import text

from app.core.security import create_access_token
from app.database.session import AsyncSessionLocal
from app.main import app
from app.services.usage.usage_quota import (
    DEFAULT_INTERVIEW_QUOTA,
    MAX_ALLOWED_QUOTA,
    QuotaExceeded,
    UsageMetric,
    current_period,
    decide_quota,
    normalize_quota,
)
from app.services.usage.usage_service import UsageService

USER_ID = 3
PROJECT_ID = 3

# 本测试使用的配额。取得比需要的场次多一点，
# 同时留出"故意用尽"的空间。
TEST_QUOTA = 3

results: list[tuple[str, bool]] = []


def record(label: str, ok: bool, extra: str = "") -> None:
    results.append((label, ok))
    suffix = f"  {extra}" if extra else ""
    print(f"  [{'OK  ' if ok else 'FAIL'}] {label}{suffix}")


def check_pure_logic() -> None:
    """决策器与账期：不依赖环境，必须先可信。"""

    print()
    print("=" * 74)
    print("1. 决策器与账期纯逻辑")
    print("=" * 74)

    record(
        f"默认配额为 {DEFAULT_INTERVIEW_QUOTA}",
        normalize_quota(None) == DEFAULT_INTERVIEW_QUOTA,
    )
    record("负数配额夹取到 0", normalize_quota(-5) == 0)
    record(
        f"超大配额夹取到 {MAX_ALLOWED_QUOTA}",
        normalize_quota(999999) == MAX_ALLOWED_QUOTA,
    )

    # 账期必须两位数补零，否则排序与唯一约束都会错
    record(
        "账期格式为 YYYY-MM 且补零",
        current_period(datetime(2026, 1, 5, tzinfo=timezone.utc))
        == "2026-01"
        and current_period(
            datetime(2026, 12, 31, tzinfo=timezone.utc)
        )
        == "2026-12",
    )

    # used 含本次动作，因此 used == quota 应当**允许**
    record(
        "用满但未超出时允许（used == quota）",
        decide_quota(10, 10, "2026-10").allowed,
    )
    record(
        "超出时拒绝（used == quota + 1）",
        not decide_quota(11, 10, "2026-10").allowed,
    )
    # 零额度是"完全不允许"，used=0 也不能通过
    record(
        "零额度时连 used=0 也拒绝",
        not decide_quota(0, 0, "2026-10").allowed,
    )
    record(
        "剩余额度计算正确",
        decide_quota(8, 10, "2026-10").remaining == 2,
    )


async def prepare_state() -> None:
    """清空本账期用量并把配额设为测试值。

    这是**测试脚手架**，直接写库而不是走接口 ——
    当前没有也不该有"修改自己配额"的接口。
    """

    async with AsyncSessionLocal() as db:
        await db.execute(
            text("DELETE FROM interview_usage WHERE user_id = :uid"),
            {"uid": USER_ID},
        )
        await db.execute(
            text(
                "UPDATE users SET interview_quota = :q WHERE id = :uid"
            ),
            {"q": TEST_QUOTA, "uid": USER_ID},
        )
        await db.commit()


async def restore_state(extra_session_ids: list[int]) -> None:
    """删除测试会话、清空用量、恢复默认配额。"""

    async with AsyncSessionLocal() as db:
        for sid in extra_session_ids:
            if sid is None:
                continue
            await db.execute(
                text("DELETE FROM interview_sessions WHERE id = :sid"),
                {"sid": sid},
            )

        await db.execute(
            text("DELETE FROM interview_usage WHERE user_id = :uid"),
            {"uid": USER_ID},
        )
        await db.execute(
            text(
                "UPDATE users SET interview_quota = :q WHERE id = :uid"
            ),
            {"q": DEFAULT_INTERVIEW_QUOTA, "uid": USER_ID},
        )
        await db.commit()


async def main():
    check_pure_logic()

    await prepare_state()

    token = create_access_token({"sub": str(USER_ID)})
    headers = {"Authorization": f"Bearer {token}"}
    transport = httpx.ASGITransport(app=app)

    created_sessions: list[int] = []

    async with httpx.AsyncClient(
        transport=transport,
        base_url="http://test",
        headers=headers,
        timeout=120.0,
    ) as client:

        # ========================================================
        # 2. 查询初始用量
        # ========================================================
        print()
        print("=" * 74)
        print("2. 查询当前用量")
        print("=" * 74)

        response = await client.get("/api/usage")
        record("用量端点返回 200", response.status_code == 200,
               f"status={response.status_code}")

        body = response.json()
        record("账期格式正确",
               body["period"] == current_period(),
               f"period={body['period']}")
        record("指标为 interview_started",
               body["interviews"]["metric"] == "interview_started",
               f"metric={body['interviews']['metric']}")
        record(f"配额为测试值 {TEST_QUOTA}",
               body["interviews"]["quota"] == TEST_QUOTA,
               f"quota={body['interviews']['quota']}")
        record("初始用量为 0（脚手架已清空）",
               body["interviews"]["used"] == 0,
               f"used={body['interviews']['used']}")
        # 响应同时带题目额度：任一先耗尽都会挡住用户，
        # 只报场次会让人以为还能用（见 §25）。
        record("同时返回题目额度",
               body["questions"]["quota"] > 0
               and "limited_by" in body,
               f"questions.quota={body['questions']['quota']} "
               f"limited_by={body['limited_by']}")

        # ========================================================
        # 3. 创建草稿**不**消费额度
        # ========================================================
        print()
        print("=" * 74)
        print("3. 创建草稿不消费额度（额度只在开始时扣）")
        print("=" * 74)

        response = await client.post(
            "/api/interviews",
            json={"project_id": PROJECT_ID, "target_role": "额度验证"},
        )
        record("创建会话返回 201", response.status_code == 201,
               f"status={response.status_code}")

        draft_id = (
            response.json()["id"]
            if response.status_code == 201
            else None
        )
        created_sessions.append(draft_id)
        record("新会话记录了所有者",
               response.status_code == 201
               and response.json().get("user_id") == USER_ID)

        response = await client.get("/api/usage")
        record("创建草稿后用量仍为 0",
               response.json()["interviews"]["used"] == 0,
               f"used={response.json()['interviews']['used']}")

        # ========================================================
        # 4. 开始面试才消费额度
        # ========================================================
        print()
        print("=" * 74)
        print("4. 开始面试消费额度（调用 LLM）")
        print("=" * 74)

        response = await client.post(
            f"/api/interviews/{draft_id}/start",
            json={"query": "额度验证"},
        )
        record("开始面试返回 200", response.status_code == 200,
               f"status={response.status_code}")

        response = await client.get("/api/usage")
        record("开始后用量 +1",
               response.json()["interviews"]["used"] == 1,
               f"used={response.json()['interviews']['used']}")
        record("剩余额度下降",
               response.json()["interviews"]["remaining"] == TEST_QUOTA - 1,
               f"remaining={response.json()['interviews']['remaining']}")

        # ========================================================
        # 5. 配额耗尽后被强制拒绝（429）
        # ========================================================
        print()
        print("=" * 74)
        print("5. 配额耗尽后开始面试被拒（429）")
        print("=" * 74)

        # 把配额压到已用量，制造"已用尽"
        async with AsyncSessionLocal() as db:
            await db.execute(
                text(
                    "UPDATE users SET interview_quota = 1 "
                    "WHERE id = :uid"
                ),
                {"uid": USER_ID},
            )
            await db.commit()

        response = await client.post(
            "/api/interviews",
            json={"project_id": PROJECT_ID},
        )
        blocked_id = response.json()["id"]
        created_sessions.append(blocked_id)

        response = await client.post(
            f"/api/interviews/{blocked_id}/start",
            json={"query": "超额验证"},
        )
        record("配额耗尽时开始面试返回 429",
               response.status_code == 429,
               f"status={response.status_code}")
        if response.status_code == 429:
            print(f"      错误信息: {response.json()['detail'][:64]}")

        # 关键：被拒绝**不能**让用量继续上涨
        response = await client.get("/api/usage")
        record("被拒绝后用量未上涨（已回滚）",
               response.json()["interviews"]["used"] == 1,
               f"used={response.json()['interviews']['used']}")

        # 被拒绝的会话不应被推进状态（否则会留下一场半开的面试）
        response = await client.get(f"/api/interviews/{blocked_id}")
        record("被拒绝的会话仍是 draft",
               response.json()["status"] == "draft",
               f"status={response.json()['status']}")

        # ========================================================
        # 6. 服务层原子性与只读性
        # ========================================================
        print()
        print("=" * 74)
        print("6. 服务层消费与只读查询")
        print("=" * 74)

        async with AsyncSessionLocal() as db:
            service = UsageService(db)

            decision = await service.consume(
                user_id=USER_ID,
                metric=UsageMetric.INTERVIEW_STARTED,
                quota=MAX_ALLOWED_QUOTA,
            )
            record("消费返回判定结果",
                   decision.allowed and decision.used > 0,
                   f"used={decision.used}")

            try:
                await service.consume(
                    user_id=USER_ID,
                    metric=UsageMetric.INTERVIEW_STARTED,
                    quota=0,
                )
                record("配额 0 时消费抛出 QuotaExceeded", False,
                       "没有抛出")
            except QuotaExceeded as exc:
                record("配额 0 时消费抛出 QuotaExceeded", True,
                       f"used={exc.used} quota={exc.quota}")

            await db.rollback()

            before = (
                await service.get_usage(
                    user_id=USER_ID,
                    metric=UsageMetric.INTERVIEW_STARTED,
                    quota=MAX_ALLOWED_QUOTA,
                )
            ).used
            await service.get_usage(
                user_id=USER_ID,
                metric=UsageMetric.INTERVIEW_STARTED,
                quota=MAX_ALLOWED_QUOTA,
            )
            after = (
                await service.get_usage(
                    user_id=USER_ID,
                    metric=UsageMetric.INTERVIEW_STARTED,
                    quota=MAX_ALLOWED_QUOTA,
                )
            ).used
            record("只读查询不改变用量", before == after,
                   f"{before} == {after}")

        # ========================================================
        # 7. 所有者不变式
        # ========================================================
        print()
        print("=" * 74)
        print("7. 所有者不变式（user_id 必须等于 project.owner_id）")
        print("=" * 74)

        from app.services.interview_session_service import (
            InterviewSessionService,
        )

        async with AsyncSessionLocal() as db:
            try:
                await InterviewSessionService(db).create_session(
                    project_id=PROJECT_ID,
                    # 故意传一个不是项目所有者的用户
                    user_id=999999,
                )
                record("为他人的项目创建会话被拒绝", False,
                       "没有抛出")
            except ValueError as exc:
                record("为他人的项目创建会话被拒绝", True,
                       str(exc)[:56])

        # ========================================================
        # 8. 清理
        # ========================================================
        print()
        print("=" * 74)

    await restore_state(created_sessions)

    print(
        f"已清理会话 {created_sessions}、本账期用量，"
        f"配额恢复为 {DEFAULT_INTERVIEW_QUOTA}"
    )

    print()
    print("=" * 74)
    passed = sum(1 for _, ok in results if ok)
    failed = [label for label, ok in results if not ok]

    print(f"通过 {passed} / {len(results)}")
    if failed:
        print("失败项:")
        for label in failed:
            print("  -", label)
        sys.exit(1)

    print("全部通过")


asyncio.run(main())
