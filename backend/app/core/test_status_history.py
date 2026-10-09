"""状态转移轨迹验证。

覆盖 §10.6 要求的可审计性：
"这场面试什么时候变成这样、由什么引起"必须可查。

最关键的一条：**失败的转移不能留下轨迹**。
若历史先独立提交而状态随后失败，轨迹里就会出现一次
"从未发生"的转移 —— 那比没有轨迹更糟，因为它会把人引向错误方向。

依赖：PostgreSQL、Milvus、LLM。
运行：uv run python -m app.core.test_status_history
"""

import asyncio
import sys

import httpx
from sqlalchemy import text

from app.core.security import create_access_token
from app.database.session import AsyncSessionLocal
from app.main import app
from app.services.interview.interview_state_machine import (
    TRANSITIONS,
    SessionStatus,
    TransitionTrigger,
    allowed_transitions,
)

USER_ID = 3
PROJECT_ID = 3

ANSWER = (
    "我负责后端。简历经 FastAPI 解析后存入 PostgreSQL，"
    "按 500 字符切块并写入 Milvus，检索时带 project_id 过滤。"
)

results: list[tuple[str, bool]] = []


def record(label: str, ok: bool, extra: str = "") -> None:
    results.append((label, ok))
    suffix = f"  {extra}" if extra else ""
    print(f"  [{'OK  ' if ok else 'FAIL'}] {label}{suffix}")


def _resume_source_for(history: list[dict], entry_id: int) -> str | None:
    """找出某条 paused 转移对应的"暂停前状态"。

    轨迹是本测试的**唯一事实来源**：暂停那一条的 `from_status`
    就是 resume_status 的值（状态机按此写入）。
    这样测试不必去查库，也不会因为读到后来被清空的
    resume_status 而误判。
    """

    for index, item in enumerate(history):
        if item["id"] != entry_id:
            continue
        # 往前找最近的 "→ paused"，它的 from_status 就是恢复目标
        for earlier in reversed(history[:index]):
            if earlier["to_status"] == "paused":
                return earlier["from_status"]
        return None

    return None


def check_trigger_enum() -> None:
    """触发原因枚举必须覆盖所有会写历史的调用点。"""

    print()
    print("=" * 74)
    print("1. 触发原因枚举")
    print("=" * 74)

    required = {
        "INTERVIEW_STARTED",
        "QUESTION_GENERATED",
        "ANSWER_SUBMITTED",
        "ANALYSIS_COMPLETED",
        "FOLLOW_UP_GENERATED",
        "EVALUATION_STARTED",
        "EVALUATION_COMPLETED",
        "BUDGET_EXHAUSTED",
        "USER_PAUSED",
        "USER_RESUMED",
        "USER_FINISHED",
        "UNSPECIFIED",
    }

    members = {item.name for item in TransitionTrigger}

    record(
        "涵盖所有已知调用点",
        required <= members,
        f"缺少={sorted(required - members)}",
    )
    record(
        "默认值为 unspecified",
        TransitionTrigger.UNSPECIFIED.value == "unspecified",
    )

    # 与终止原因的**值**可以重叠，而且应当重叠：
    # `budget_exhausted` 在两组里指的是同一件事
    # （面试因预算结束 / 这次转移因预算发生）。
    # 两组枚举回答不同问题，不要为了"看起来不重复"而改名 ——
    # 那会让同一个概念有两个名字。
    from app.services.interview.interview_termination import (
        TerminationReason,
    )

    budget_pair = (
        TransitionTrigger.BUDGET_EXHAUSTED.value
        == TerminationReason.BUDGET_EXHAUSTED.value
    )
    record(
        "与终止原因对同一概念使用同一名字",
        budget_pair,
        (
            f"{TransitionTrigger.BUDGET_EXHAUSTED.value} / "
            f"{TerminationReason.BUDGET_EXHAUSTED.value}"
        ),
    )


async def main():
    check_trigger_enum()

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
        # 2. 新建会话
        # ========================================================
        print()
        print("=" * 74)
        print("2. 新建会话")
        print("=" * 74)

        response = await client.post(
            "/api/interviews",
            json={
                "project_id": PROJECT_ID,
                "target_role": "后端工程师（轨迹验证）",
            },
        )
        session_id = response.json()["id"]
        print(f"      session_id = {session_id}")

        response = await client.get(
            f"/api/interviews/{session_id}/history"
        )
        record("轨迹端点返回 200",
               response.status_code == 200,
               f"status={response.status_code}")
        record("新建会话尚无转移记录",
               response.json() == [],
               f"共 {len(response.json())} 条")

        # ========================================================
        # 3. 失败的转移不能留下轨迹
        # ========================================================
        print()
        print("=" * 74)
        print("3. 失败的转移不留下轨迹（关键用例）")
        print("=" * 74)

        response = await client.post(
            f"/api/interviews/{session_id}/transition",
            json={"target_status": "completed"},
        )
        record("非法转移被拒（409）",
               response.status_code == 409,
               f"status={response.status_code}")

        response = await client.get(
            f"/api/interviews/{session_id}/history"
        )
        record("被拒的转移没有留下轨迹",
               response.json() == [],
               f"共 {len(response.json())} 条")

        # ========================================================
        # 4. 开始面试 → 生成首题
        # ========================================================
        print()
        print("=" * 74)
        print("4. 开始面试与生成首题")
        print("=" * 74)

        response = await client.post(
            f"/api/interviews/{session_id}/start",
            json={"query": "考察后端与 RAG"},
        )
        record("开始面试返回 200", response.status_code == 200)
        question = response.json()["question"]

        response = await client.get(
            f"/api/interviews/{session_id}/history"
        )
        history = response.json()
        pairs = [
            (item["from_status"], item["to_status"])
            for item in history
        ]
        triggers = [item["trigger"] for item in history]

        record(
            "记录了 draft → preparing_context → planned → asking",
            ("draft", "preparing_context") in pairs
            and ("preparing_context", "planned") in pairs
            and ("planned", "asking") in pairs,
            f"{pairs}",
        )
        record(
            "开始两个中间步的触发原因是 interview_started",
            triggers.count("interview_started") == 2,
            f"count={triggers.count('interview_started')}",
        )
        record(
            "生成首题的触发原因是 question_generated",
            "question_generated" in triggers,
        )

        # ========================================================
        # 5. 答题 → 追问
        # ========================================================
        print()
        print("=" * 74)
        print("5. 答题与追问")
        print("=" * 74)

        response = await client.post(
            f"/api/interviews/{session_id}/answer",
            json={"question_id": question["id"], "answer": ANSWER},
        )
        record("作答返回 200", response.status_code == 200)
        follow_up = response.json()["follow_up_question"]
        record("生成了追问", follow_up is not None)

        response = await client.get(
            f"/api/interviews/{session_id}/history"
        )
        history = response.json()
        triggers = [item["trigger"] for item in history]

        record("记录了 answer_submitted",
               "answer_submitted" in triggers)
        record("记录了 analysis_completed",
               "analysis_completed" in triggers)
        record("记录了 follow_up_generated",
               "follow_up_generated" in triggers)

        # 一轮答题的中间态必须**逐步**可读，而不是折叠成
        # 一条无法解释的 asking → asking。
        #
        # 这是轨迹的价值所在：asking 到 asking 之间发生了什么，
        # 只有记录了中间态才答得出来。
        turn_chain = [
            (item["from_status"], item["to_status"], item["trigger"])
            for item in history
        ]
        expected_chain = [
            ("asking", "waiting_for_answer", "answer_submitted"),
            ("waiting_for_answer", "evaluating", "analysis_completed"),
            ("evaluating", "asking", "follow_up_generated"),
        ]
        record(
            "一轮答题的三个中间态逐步可见",
            all(step in turn_chain for step in expected_chain),
            f"缺少={[s for s in expected_chain if s not in turn_chain]}"
            if not all(s in turn_chain for s in expected_chain)
            else "",
        )

        # ========================================================
        # 6. 暂停 / 恢复
        # ========================================================
        print()
        print("=" * 74)
        print("6. 暂停与恢复")
        print("=" * 74)

        await client.post(f"/api/interviews/{session_id}/pause")
        await client.post(f"/api/interviews/{session_id}/resume")

        response = await client.get(
            f"/api/interviews/{session_id}/history"
        )
        triggers = [item["trigger"] for item in response.json()]

        record("记录了 user_paused", "user_paused" in triggers)
        record("记录了 user_resumed", "user_resumed" in triggers)

        # ========================================================
        # 7. 结束面试
        # ========================================================
        print()
        print("=" * 74)
        print("7. 结束面试（调用 LLM）")
        print("=" * 74)

        response = await client.post(
            f"/api/interviews/{session_id}/finish"
        )
        record("结束返回 200", response.status_code == 200,
               f"status={response.status_code}")
        record("状态为 completed",
               response.json().get("session_status") == "completed")

        response = await client.get(
            f"/api/interviews/{session_id}/history"
        )
        history = response.json()
        triggers = [item["trigger"] for item in history]
        pairs = [
            (item["from_status"], item["to_status"])
            for item in history
        ]

        record("记录了 evaluation_started",
               "evaluation_started" in triggers)
        record("记录了 evaluation_completed",
               "evaluation_completed" in triggers)
        record("最终状态为 completed",
               history[-1]["to_status"] == "completed",
               f"最后一条={history[-1]['to_status']}")

        # ========================================================
        # 8. 轨迹自身的完整性
        # ========================================================
        print()
        print("=" * 74)
        print("8. 轨迹的完整性")
        print("=" * 74)

        record("没有自转移记录",
               all(a != b for a, b in pairs))

        # 除首条外，每条 from_status 应等于上一条 to_status
        # （asking → asking 也满足这个不变式）
        chained = all(
            history[i]["from_status"] == history[i - 1]["to_status"]
            for i in range(1, len(history))
        )
        record("轨迹首尾相接（无缺口）", chained,
               f"共 {len(history)} 条")

        # 每条 to_status 都必须是已知状态（含旧值）
        known = {item.value for item in SessionStatus} | {"created"}
        record("所有状态值合法",
               all(
                   item["from_status"] in known
                   and item["to_status"] in known
                   for item in history
               ))

        # 每条转移都必须是状态表允许的。
        #
        # ⚠️ 不能只查静态表：`paused` 的可恢复目标是**动态**的
        # （由 resume_status 决定，ADR-027），静态表里只有 cancelled。
        # 只查静态表会把合法的 paused → asking 误判为非法。
        illegal = []
        for item in history:
            if item["from_status"] == item["to_status"]:
                continue
            try:
                current = SessionStatus(item["from_status"])
                target = SessionStatus(item["to_status"])
            except ValueError:
                # 历史旧值（如 "created"）不在枚举里，跳过
                continue

            if current is SessionStatus.PAUSED:
                # paused 的恢复目标来自暂停前的状态。
                # 轨迹里前一条的 from_status 就是它。
                allowed = allowed_transitions(
                    current,
                    resume_status=_resume_source_for(
                        history, item["id"]
                    ),
                )
            else:
                allowed = TRANSITIONS[current]

            if target not in allowed:
                illegal.append(
                    (item["from_status"], item["to_status"])
                )
        record("所有转移都合法（对照状态表与动态恢复目标）",
               not illegal,
               f"非法={illegal}" if illegal else "")

        print()
        print("      完整轨迹：")
        for item in history:
            print(
                f"        {item['from_status']:>18} → "
                f"{item['to_status']:<18} "
                f"[{item['trigger']}]"
            )

        # ========================================================
        # 9. 越权与不存在
        # ========================================================
        print()
        print("=" * 74)
        print("9. 访问控制")
        print("=" * 74)

        response = await client.get(
            "/api/interviews/999999/history"
        )
        record("不存在的会话返回 404",
               response.status_code == 404,
               f"status={response.status_code}")

        # ========================================================
        # 10. 清理
        # ========================================================
        print()
        print("=" * 74)

    async with AsyncSessionLocal() as db:
        # 轨迹随会话级联删除，这里顺带验证一下
        before = (
            await db.execute(
                text(
                    "SELECT COUNT(*) FROM interview_status_history "
                    "WHERE session_id = :sid"
                ),
                {"sid": session_id},
            )
        ).scalar_one()

        await db.execute(
            text("DELETE FROM interview_sessions WHERE id = :sid"),
            {"sid": session_id},
        )
        await db.commit()

        after = (
            await db.execute(
                text(
                    "SELECT COUNT(*) FROM interview_status_history "
                    "WHERE session_id = :sid"
                ),
                {"sid": session_id},
            )
        ).scalar_one()

    record("删除会话时轨迹级联删除",
           before > 0 and after == 0,
           f"{before} -> {after}")

    print(f"      清理前 {before} 条轨迹，清理后 {after} 条")

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
