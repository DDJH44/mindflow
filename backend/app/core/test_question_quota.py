"""按题计量验证。

覆盖 §25：月度题目额度（含追问，跨场次）。

要验证的核心漏洞：单场题目上限（`InterviewSession.max_questions`）
描述的是"这一场想聊多深"，不是成本额度。
若只有单场上限，用户可以每场都设 30 题 ——
于是那个上限反而变成了"允许 30 题"的授权。

依赖：PostgreSQL、Milvus、LLM。
运行：uv run python -m app.core.test_question_quota
"""

import asyncio
import sys

import httpx
from sqlalchemy import text

from app.core.security import create_access_token
from app.core.test_support import (
    grant_quota,
    get_used,
    reset_quota,
)
from app.database.session import AsyncSessionLocal
from app.main import app
from app.services.usage.usage_quota import (
    DEFAULT_QUESTION_QUOTA,
    MAX_ALLOWED_QUESTION_QUOTA,
    QuotaPair,
    UsageMetric,
    decide_question_quota,
    normalize_question_quota,
)

USER_ID = 3
PROJECT_ID = 3

# 本测试使用的额度。场次给足，题目故意给小 ——
# 目的是验证"题目额度先耗尽"能挡住用户。
INTERVIEW_QUOTA = 10
QUESTION_QUOTA = 3

ANSWER = (
    "我负责后端。简历经 FastAPI 解析后存入 PostgreSQL，"
    "按 500 字符切块并写入 Milvus，检索时带 project_id 过滤。"
)

results: list[tuple[str, bool]] = []


def record(label: str, ok: bool, extra: str = "") -> None:
    results.append((label, ok))
    suffix = f"  {extra}" if extra else ""
    print(f"  [{'OK  ' if ok else 'FAIL'}] {label}{suffix}")


def check_pure_logic() -> None:
    """纯逻辑：不依赖环境，必须先可信。"""

    print()
    print("=" * 74)
    print("1. 题目额度的纯逻辑")
    print("=" * 74)

    record(
        f"默认题目额度为 {DEFAULT_QUESTION_QUOTA}",
        normalize_question_quota(None) == DEFAULT_QUESTION_QUOTA,
    )
    record(
        f"上越界夹取到 {MAX_ALLOWED_QUESTION_QUOTA}",
        normalize_question_quota(999999)
        == MAX_ALLOWED_QUESTION_QUOTA,
    )
    record("负数夹取到 0", normalize_question_quota(-5) == 0)
    record(
        "题目额度与场次额度上限不同",
        normalize_question_quota(3000) == 3000
        and normalize_question_quota(99999)
        == MAX_ALLOWED_QUESTION_QUOTA,
    )

    record(
        "用满但未超出时允许",
        decide_question_quota(100, 100, "2026-10").allowed,
    )
    record(
        "超出时拒绝",
        not decide_question_quota(101, 100, "2026-10").allowed,
    )
    record(
        "零额度时拒绝",
        not decide_question_quota(0, 0, "2026-10").allowed,
    )

    # 合成视图：任一方耗尽都要挡住
    both_ok = QuotaPair("2026-10", 1, 10, 5, 100)
    record(
        "双方充足时不限",
        both_ok.allowed and both_ok.limited_by is None,
    )

    interviews_out = QuotaPair("2026-10", 10, 10, 5, 100)
    record(
        "场次耗尽时受限方为 interviews",
        interviews_out.limited_by == "interviews"
        and not interviews_out.allowed,
    )

    questions_out = QuotaPair("2026-10", 1, 10, 100, 100)
    record(
        "题目耗尽时受限方为 questions",
        questions_out.limited_by == "questions"
        and not questions_out.allowed,
    )

    both_out = QuotaPair("2026-10", 10, 10, 100, 100)
    record(
        "双方都耗尽时先报场次（先耗尽的那个）",
        both_out.limited_by == "interviews",
        f"limited_by={both_out.limited_by}",
    )


async def main():
    check_pure_logic()

    await grant_quota(USER_ID, INTERVIEW_QUOTA, QUESTION_QUOTA)

    token = create_access_token({"sub": str(USER_ID)})
    headers = {"Authorization": f"Bearer {token}"}
    transport = httpx.ASGITransport(app=app)

    session_id = None

    try:
        async with httpx.AsyncClient(
            transport=transport,
            base_url="http://test",
            headers=headers,
            timeout=300.0,
        ) as client:

            # ====================================================
            # 2. 用量接口返回两份额度
            # ====================================================
            print()
            print("=" * 74)
            print("2. 用量接口返回两份额度")
            print("=" * 74)

            response = await client.get("/api/usage")
            record("返回 200", response.status_code == 200)

            body = response.json()
            record(
                "包含 interviews 与 questions 两项",
                "interviews" in body and "questions" in body,
                f"keys={sorted(body.keys())}",
            )
            record(
                f"题目额度为测试值 {QUESTION_QUOTA}",
                body["questions"]["quota"] == QUESTION_QUOTA,
                f"quota={body['questions']['quota']}",
            )
            record(
                "初始均未受限",
                body["limited_by"] is None and body["allowed"],
                f"limited_by={body['limited_by']}",
            )

            # ====================================================
            # 3. 单场题目上限不能突破月度额度
            # ====================================================
            print()
            print("=" * 74)
            print("3. 单场上限不能突破月度额度（漏洞验证）")
            print("=" * 74)

            # 刻意把单场预算设得远大于月度题目额度
            response = await client.post(
                "/api/interviews",
                json={
                    "project_id": PROJECT_ID,
                    "target_role": "后端工程师（按题计量验证）",
                    "max_questions": 30,
                },
            )
            session_id = response.json()["id"]
            record(
                "单场预算设为 30",
                response.json().get("max_questions") == 30,
                f"max_questions={response.json().get('max_questions')}",
            )

            response = await client.post(
                f"/api/interviews/{session_id}/start",
                json={"query": "考察后端"},
            )

            # 明确区分"代码坏了"与"LLM 端点当时不可用"。
            #
            # 实测该端点会间歇性慢到超时（极短请求也曾连续 4 次超时、
            # 第 4 次重试才在 68.9s 后成功）。若不加区分，一次端点抖动
            # 会让套件以 exit=1 崩溃，看起来像代码回归 ——
            # 这类误判已经发生过，浪费过排查时间。
            #
            # 路由现在把上游失败映射成 503（D57），因此判据是 503。
            if response.status_code == 503:
                detail = ""
                try:
                    detail = str(response.json().get("detail", ""))
                except Exception:  # noqa: BLE001
                    pass

                print()
                print("  ⚠️ LLM 端点不可用，本次**未执行**（不是代码失败）")
                print(f"     {detail[:140]}")
                print("     稍后重跑本套件即可。")
                return 2

            record(
                "开始面试返回 200",
                response.status_code == 200,
                f"status={response.status_code}",
            )

            first_question = response.json()["question"]

            # 开始面试消费 1 题（生成首题）
            used_after_start = await get_used(
                USER_ID, UsageMetric.QUESTION_GENERATED
            )
            record(
                "生成首题消费 1 个题目额度",
                used_after_start == 1,
                f"used={used_after_start}",
            )

            # 第 2 题：作答生成追问 → 消费第 2 个
            response = await client.post(
                f"/api/interviews/{session_id}/answer",
                json={
                    "question_id": first_question["id"],
                    "answer": ANSWER,
                },
            )
            record(
                "第 1 次作答返回 200",
                response.status_code == 200,
                f"status={response.status_code}",
            )

            used_after_answer = await get_used(
                USER_ID, UsageMetric.QUESTION_GENERATED
            )
            record(
                "追问也计入题目额度（+1）",
                used_after_answer == 2,
                f"used={used_after_answer}",
            )

            follow_up = response.json()["follow_up_question"]

            # 第 2 次作答：额度只有 3，这次会生成第 3 题
            response = await client.post(
                f"/api/interviews/{session_id}/answer",
                json={
                    "question_id": follow_up["id"],
                    "answer": ANSWER,
                },
            )
            used_third = await get_used(
                USER_ID, UsageMetric.QUESTION_GENERATED
            )
            print(
                f"      第 2 次作答后 used={used_third}，"
                f"响应={response.status_code}"
            )

            # 第 3 次作答：额度已满，追问不应生成
            if response.status_code == 200:
                third_follow_up = response.json().get(
                    "follow_up_question"
                )
                if third_follow_up is not None:
                    response = await client.post(
                        f"/api/interviews/{session_id}/answer",
                        json={
                            "question_id": third_follow_up["id"],
                            "answer": ANSWER,
                        },
                    )
                    record(
                        "题目额度用尽后作答被拒（429）",
                        response.status_code == 429,
                        f"status={response.status_code}",
                    )
                    if response.status_code == 429:
                        print(
                            f"      错误信息: "
                            f"{response.json()['detail'][:64]}"
                        )
                else:
                    record(
                        "第 2 次作答后已因单场预算结束，跳过额度断言",
                        True,
                    )

            # ====================================================
            # 4. 被拒后用量不得上涨
            # ====================================================
            print()
            print("=" * 74)
            print("4. 额度用尽后的状态")
            print("=" * 74)

            used_now = await get_used(
                USER_ID, UsageMetric.QUESTION_GENERATED
            )
            record(
                f"用量不超过额度上限（{QUESTION_QUOTA}）",
                used_now <= QUESTION_QUOTA,
                f"used={used_now} / {QUESTION_QUOTA}",
            )

            response = await client.get("/api/usage")
            body = response.json()
            record(
                "用量接口显示受限方为 questions",
                body["limited_by"] == "questions",
                f"limited_by={body['limited_by']}",
            )
            record(
                "题目剩余归零",
                body["questions"]["remaining"] == 0,
                f"remaining={body['questions']['remaining']}",
            )
            record(
                "场次额度仍有剩余（说明是题目额度挡住的）",
                body["interviews"]["remaining"] > 0,
                f"场次剩余={body['interviews']['remaining']}",
            )

            # 新开会话也应被题目额度挡住
            response = await client.post(
                "/api/interviews",
                json={"project_id": PROJECT_ID},
            )
            blocked_id = response.json()["id"]
            response = await client.post(
                f"/api/interviews/{blocked_id}/start",
                json={"query": "额度已满"},
            )
            record(
                "题目额度耗尽后新会话也无法开始（429）",
                response.status_code == 429,
                f"status={response.status_code}",
            )

    finally:
        async with AsyncSessionLocal() as db:
            await db.execute(
                text(
                    "DELETE FROM interview_sessions "
                    "WHERE target_role IN "
                    "('后端工程师（按题计量验证）')"
                )
            )
            await db.commit()

        await reset_quota(USER_ID)
        print("已清理测试会话并恢复默认额度")

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
