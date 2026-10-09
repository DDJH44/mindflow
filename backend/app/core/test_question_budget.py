"""题目预算与终止条件验证。

覆盖 §9.3 要求的"每轮题目预算与终止条件"：
服务端必须能强制结束一场面试，而不是只能等用户点结束。

测试手法：把 `max_questions` 设成 2，然后连续作答，
验证第 2 次作答后自动结束并带回整场评价。

依赖：PostgreSQL、Milvus、LLM 均可用。
运行：uv run python -m app.core.test_question_budget
"""

import asyncio
import sys

import httpx
from sqlalchemy import text

from app.core.security import create_access_token
from app.core.test_support import grant_quota, reset_quota
from app.database.session import AsyncSessionLocal
from app.main import app
from app.services.interview.interview_termination import (
    DEFAULT_MAX_QUESTIONS,
    MAX_ALLOWED_QUESTIONS,
    MIN_ALLOWED_QUESTIONS,
    decide_continue,
    normalize_max_questions,
)

USER_ID = 3
PROJECT_ID = 3
BUDGET = 2

# 本测试要开始的面试场次
QUOTA = 5

ANSWER = (
    "我负责后端。用户上传简历后 FastAPI 解析并存入 PostgreSQL，"
    "按 500 字符切块后用 text-embedding-v4 写入 Milvus，"
    "检索时带 project_id 过滤取 top-k 再回表取正文。"
)

results: list[tuple[str, bool]] = []


def record(label: str, ok: bool, extra: str = "") -> None:
    results.append((label, ok))
    suffix = f"  {extra}" if extra else ""
    print(f"  [{'OK  ' if ok else 'FAIL'}] {label}{suffix}")


def check_decision_logic() -> None:
    """先跑纯逻辑：决策器不依赖环境，必须最先可信。"""

    print()
    print("=" * 74)
    print("1. 决策器纯逻辑")
    print("=" * 74)

    record(
        f"默认预算为 {DEFAULT_MAX_QUESTIONS}",
        normalize_max_questions(None) == DEFAULT_MAX_QUESTIONS,
    )
    record(
        f"下越界夹取到 {MIN_ALLOWED_QUESTIONS}",
        normalize_max_questions(0) == MIN_ALLOWED_QUESTIONS,
    )
    record(
        f"上越界夹取到 {MAX_ALLOWED_QUESTIONS}",
        normalize_max_questions(999) == MAX_ALLOWED_QUESTIONS,
    )
    record(
        "未达预算时继续",
        decide_continue(
            questions_asked=1, max_questions=2
        ).should_continue,
    )
    exhausted = decide_continue(
        questions_asked=2, max_questions=2
    )
    record(
        "达到预算时停止且带原因",
        not exhausted.should_continue
        and exhausted.reason is not None,
        f"reason={exhausted.reason.value if exhausted.reason else None}",
    )


async def main():
    # 自备配额：本测试会 start 面试，消费一次额度。
    # 依赖外部残留状态会让失败原因指向错误的地方（见 D38）。
    await grant_quota(USER_ID, QUOTA)

    check_decision_logic()

    token = create_access_token({"sub": str(USER_ID)})
    headers = {"Authorization": f"Bearer {token}"}
    transport = httpx.ASGITransport(app=app)
    session_id = None

    async with httpx.AsyncClient(
        transport=transport,
        base_url="http://test",
        headers=headers,
        timeout=300.0,
    ) as client:

        # ========================================================
        # 2. 建会话并设小预算
        # ========================================================
        print()
        print("=" * 74)
        print(f"2. 建会话并把预算设为 {BUDGET}")
        print("=" * 74)

        response = await client.post(
            "/api/interviews",
            json={
                "project_id": PROJECT_ID,
                "interview_type": "technical",
                "target_role": "后端工程师（预算验证）",
                "max_questions": BUDGET,
            },
        )
        record("创建会话返回 201",
               response.status_code == 201,
               f"status={response.status_code}")

        body = response.json()
        session_id = body["id"]
        record(f"预算已保存为 {BUDGET}",
               body.get("max_questions") == BUDGET,
               f"max_questions={body.get('max_questions')}")
        record("已问题目数为 0",
               body.get("questions_asked") == 0)
        print(f"      session_id = {session_id}")

        # ========================================================
        # 3. 开始面试
        # ========================================================
        print()
        print("=" * 74)
        print("3. 开始面试（第 1 题）")
        print("=" * 74)

        response = await client.post(
            f"/api/interviews/{session_id}/start",
            json={"query": "考察后端与 RAG"},
        )
        record("开始面试返回 200", response.status_code == 200)

        start_body = response.json()
        question = start_body["question"]
        record("已问题目数变为 1",
               start_body["current_question_index"] == 1,
               f"index={start_body['current_question_index']}")

        # ========================================================
        # 4. 第 1 题作答：应生成追问（预算未满）
        # ========================================================
        print()
        print("=" * 74)
        print("4. 第 1 题作答：预算未满，应生成追问")
        print("=" * 74)

        response = await client.post(
            f"/api/interviews/{session_id}/answer",
            json={"question_id": question["id"], "answer": ANSWER},
        )
        record("作答返回 200", response.status_code == 200,
               f"status={response.status_code}")

        first = response.json()
        record("未结束（finished=False）",
               first.get("finished") is False,
               f"finished={first.get('finished')}")
        record("生成了追问",
               first.get("follow_up_question") is not None)
        record("无评价返回", first.get("evaluation") is None)

        follow_up = first["follow_up_question"]

        # ========================================================
        # 5. 第 2 题作答：预算耗尽，应自动结束并出评价
        # ========================================================
        print()
        print("=" * 74)
        print("5. 第 2 题作答：预算耗尽，应自动结束")
        print("=" * 74)

        response = await client.post(
            f"/api/interviews/{session_id}/answer",
            json={"question_id": follow_up["id"], "answer": ANSWER},
        )
        record("作答返回 200", response.status_code == 200,
               f"status={response.status_code}")

        if response.status_code != 200:
            print("      响应体:", response.text[:400])
        else:
            second = response.json()
            record("标记为已结束",
                   second.get("finished") is True,
                   f"finished={second.get('finished')}")
            record("没有下一题",
                   second.get("follow_up_question") is None)
            record("终止原因为预算耗尽",
                   second.get("termination_reason")
                   == "budget_exhausted",
                   f"reason={second.get('termination_reason')}")
            record("会话状态为 completed",
                   second.get("session_status") == "completed",
                   f"status={second.get('session_status')}")
            record("随作答带回整场评价",
                   second.get("evaluation") is not None)

            evaluation = second.get("evaluation") or {}
            if evaluation:
                record(
                    "评价含四项分数",
                    all(
                        isinstance(evaluation.get(key), int)
                        for key in (
                            "overall_score",
                            "technical_score",
                            "project_score",
                            "communication_score",
                        )
                    ),
                    f"{evaluation.get('overall_score')}/"
                    f"{evaluation.get('technical_score')}/"
                    f"{evaluation.get('project_score')}/"
                    f"{evaluation.get('communication_score')}",
                )
                print(
                    f"      strengths={len(evaluation.get('strengths') or [])} "
                    f"weaknesses={len(evaluation.get('weaknesses') or [])} "
                    f"suggestions={len(evaluation.get('suggestions') or [])}"
                )

        # ========================================================
        # 6. 结束后状态与计数
        # ========================================================
        print()
        print("=" * 74)
        print("6. 结束后的库状态")
        print("=" * 74)

        response = await client.get(
            f"/api/interviews/{session_id}/detail"
        )
        detail = response.json()
        session = detail["session"]

        record("状态为 completed",
               session["status"] == "completed",
               f"status={session['status']}")
        record("终止原因已持久化",
               session.get("termination_reason")
               == "budget_exhausted",
               f"reason={session.get('termination_reason')}")
        record(f"题目数为预算上限 {BUDGET}",
               len(detail["questions"]) == BUDGET,
               f"共 {len(detail['questions'])} 题")
        record("评价已入库",
               detail.get("evaluation") is not None)

        # completed 后不能再作答
        response = await client.post(
            f"/api/interviews/{session_id}/answer",
            json={"question_id": question["id"], "answer": "终态"},
        )
        record("结束后作答返回 409",
               response.status_code == 409,
               f"status={response.status_code}")

        # 轨迹必须显示"系统因预算结束"，而不是"用户主动结束"。
        # 若一律写 user_finished，审计记录就在说谎 ——
        # 复盘时会以为是用户点的结束。
        response = await client.get(
            f"/api/interviews/{session_id}/history"
        )
        triggers = [item["trigger"] for item in response.json()]
        record("轨迹记录了 budget_exhausted",
               "budget_exhausted" in triggers,
               f"triggers={triggers}")
        record("轨迹没有误记为 user_finished",
               "user_finished" not in triggers)

        # ========================================================
        # 7. 预算的边界与协议约束
        # ========================================================
        print()
        print("=" * 74)
        print("7. 预算的边界与协议约束")
        print("=" * 74)

        # 超过 schema 上限：由 pydantic 拦下（422），
        # 不会走到服务端的夹取逻辑。
        response = await client.post(
            "/api/interviews",
            json={"project_id": PROJECT_ID, "max_questions": 99999},
        )
        record("超过协议上限返回 422",
               response.status_code == 422,
               f"status={response.status_code}")
        oversized_id = None

        # 边界值：schema 允许的最大值应被原样接受
        response = await client.post(
            "/api/interviews",
            json={
                "project_id": PROJECT_ID,
                "max_questions": MAX_ALLOWED_QUESTIONS,
            },
        )
        record(f"边界值 {MAX_ALLOWED_QUESTIONS} 被接受",
               response.status_code == 201
               and response.json().get("max_questions")
               == MAX_ALLOWED_QUESTIONS,
               f"max_questions="
               f"{response.json().get('max_questions')}")
        if response.status_code == 201:
            oversized_id = response.json()["id"]

        # 不传预算时用服务端默认值
        response = await client.post(
            "/api/interviews",
            json={"project_id": PROJECT_ID},
        )
        default_id = response.json()["id"]
        record(f"不传预算时用默认值 {DEFAULT_MAX_QUESTIONS}",
               response.json().get("max_questions")
               == DEFAULT_MAX_QUESTIONS,
               f"max_questions="
               f"{response.json().get('max_questions')}")

        # ========================================================
        # 8. 清理
        # ========================================================
        print()
        print("=" * 74)

    async with AsyncSessionLocal() as db:
        for target in (session_id, oversized_id, default_id):
            if target is None:
                continue
            await db.execute(
                text(
                    "DELETE FROM interview_sessions WHERE id = :sid"
                ),
                {"sid": target},
            )
        await db.commit()

    print(
        f"已清理验证会话 {session_id} / {oversized_id} / {default_id}"
    )

    # 恢复默认配额，避免影响后续脚本
    await reset_quota(USER_ID)

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
