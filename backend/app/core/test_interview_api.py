"""面试 API 层验证。

用真实 JWT 与真实数据库，通过 ASGI 传输直接调用 HTTP 端点，
覆盖路由、请求校验、响应序列化与所有权检查 ——
这些是 `test_interview_flow.py`（直接调 service）测不到的。

依赖：PostgreSQL、Milvus、LLM 均可用。
运行：uv run python -m app.core.test_interview_api
"""

import asyncio
import sys

import httpx
from sqlalchemy import text

from app.core.security import create_access_token
from app.core.test_support import grant_quota, reset_quota
from app.database.session import AsyncSessionLocal
from app.main import app

USER_ID = 3
PROJECT_ID = 3

# 本测试要开始的面试场次（每个消费一次额度）。
# 取得比实际需要多一点，留出重试空间。
QUOTA = 10

ANSWER = (
    "我负责 MindFlow 的后端。用户上传简历后由 FastAPI 解析，"
    "内容存入 PostgreSQL 并按 500 字符、overlap 100 切分成 Chunk，"
    "再用 text-embedding-v4 转成 1024 维向量写入 Milvus。"
    "检索时对 query 做同样处理，带 project_id 过滤取 top-k，"
    "按 chunk id 回 PostgreSQL 取正文。"
)

results: list[tuple[str, bool]] = []


def record(label: str, ok: bool, extra: str = "") -> None:
    results.append((label, ok))
    suffix = f"  {extra}" if extra else ""
    print(f"  [{'OK  ' if ok else 'FAIL'}] {label}{suffix}")


async def main():
    # 自备配额：本测试会创建多个会话，每个成功 start 都消费一次额度。
    # 若依赖"数据库恰好还有额度"，其它脚本的累计消耗会让这里
    # 以 429 失败，而错误信息看起来像"开始面试坏了"（见 D38）。
    await grant_quota(USER_ID, QUOTA)

    token = create_access_token({"sub": str(USER_ID)})
    headers = {"Authorization": f"Bearer {token}"}

    transport = httpx.ASGITransport(app=app)

    # 两个 id 必须在 try 之前初始化：清理在 finally 里，
    # 若中途失败时它们还没被赋值，清理本身会抛 NameError
    # 并掩盖真正的失败原因。
    session_id = None
    fresh_id = None

    async with httpx.AsyncClient(
        transport=transport,
        base_url="http://test",
        headers=headers,
        timeout=180.0,
    ) as client:
        try:
            # ========================================================
            # 1. 认证与所有权
            # ========================================================
            print()
            print("=" * 74)
            print("1. 认证与所有权检查")
            print("=" * 74)

            response = await client.post(
                "/api/interviews",
                json={
                    "project_id": PROJECT_ID,
                    "interview_type": "technical",
                },
                headers={"Authorization": "Bearer invalid-token"},
            )
            record(
                "无效 Token 返回 401",
                response.status_code == 401,
                f"status={response.status_code}",
            )

            response = await client.post(
                "/api/interviews",
                json={
                    "project_id": 999999,
                    "interview_type": "technical",
                },
            )
            record(
                "不存在的项目返回 404",
                response.status_code == 404,
                f"status={response.status_code}",
            )

            # 用一个不属于当前用户的项目验证所有权
            async with AsyncSessionLocal() as db:
                other = (
                    await db.execute(
                        text(
                            "SELECT id FROM projects "
                            "WHERE owner_id <> :uid LIMIT 1"
                        ),
                        {"uid": USER_ID},
                    )
                ).scalar()

            if other:
                response = await client.post(
                    "/api/interviews",
                    json={"project_id": other},
                )
                record(
                    "他人项目返回 404",
                    response.status_code == 404,
                    f"status={response.status_code}",
                )
            else:
                print("      （库中没有他人项目，跳过所有权用例）")

            # ========================================================
            # 2. 创建会话
            # ========================================================
            print()
            print("=" * 74)
            print("2. 创建会话")
            print("=" * 74)

            response = await client.post(
                "/api/interviews",
                json={
                    "project_id": PROJECT_ID,
                    "interview_type": "technical",
                    "target_role": "后端工程师（API 验证）",
                },
            )
            record(
                "创建返回 201",
                response.status_code == 201,
                f"status={response.status_code}",
            )

            body = response.json()
            session_id = body.get("id")
            record(
                "初始状态为 draft",
                body.get("status") == "draft",
                f"status={body.get('status')}",
            )
            record(
                "target_role 已回显",
                body.get("target_role") == "后端工程师（API 验证）",
            )
            record(
                "记录了会话所有者",
                body.get("user_id") == USER_ID,
                f"user_id={body.get('user_id')}",
            )
            print(f"      session_id = {session_id}")

            # ========================================================
            # 3. 非法转移 -> 409
            # ========================================================
            print()
            print("=" * 74)
            print("3. 非法状态转移返回 409")
            print("=" * 74)

            response = await client.post(
                f"/api/interviews/{session_id}/transition",
                json={"target_status": "completed"},
            )
            record(
                "draft → completed 返回 409",
                response.status_code == 409,
                f"status={response.status_code}",
            )

            # 非法枚举值由 pydantic 拦下 -> 422
            response = await client.post(
                f"/api/interviews/{session_id}/transition",
                json={"target_status": "not-a-status"},
            )
            record(
                "未知状态值返回 422",
                response.status_code == 422,
                f"status={response.status_code}",
            )

            # 确认库状态未被改坏
            response = await client.get(
                f"/api/interviews/{session_id}"
            )
            record(
                "非法转移后状态仍为 draft",
                response.json().get("status") == "draft",
            )

            # 失败的转移不能留下轨迹（可审计性的关键约束）
            response = await client.get(
                f"/api/interviews/{session_id}/history"
            )
            record(
                "被拒的转移没有留下轨迹",
                response.json() == [],
                f"共 {len(response.json())} 条",
            )

            # ========================================================
            # 4. 开始面试（生成首题）
            # ========================================================
            #
            # 顺序刻意如此：本节**不手动做状态转移**，
            # 让 `/start` 从 draft 自己走完
            # preparing_context → planned → asking。
            #
            # 先前版本为了给"暂停"准备一个 planned 会话，
            # 手工调了两次 transition —— 结果 `/start` 发现
            # "已在 planned"、没有中间步要执行，
            # `interview_started` 就从未被记录，
            # 第 9 节的轨迹断言因此失败。
            # 真实用户不会手动做转移，测试也不该。
            print()
            print("=" * 74)
            print("4. 开始面试：生成首题（调用向量检索 + LLM）")
            print("=" * 74)

            response = await client.post(
                f"/api/interviews/{session_id}/start",
                json={"query": "考察后端与 RAG 实践经验"},
            )
            record(
                "开始面试返回 200",
                response.status_code == 200,
                f"status={response.status_code}",
            )

            question = None

            if response.status_code != 200:
                print("      响应体:", response.text[:400])
            else:
                body = response.json()
                question = body["question"]
                record(
                    "开始后状态为 asking",
                    body["session_status"] == "asking",
                    f"status={body['session_status']}",
                )
                record("返回首题", bool(question["question"]))
                record(
                    "首题带资料依据",
                    bool(question["evidence_chunk_ids"]),
                    f"evidence={question['evidence_chunk_ids']}",
                )
                record(
                    "索引已前进为 1",
                    body["current_question_index"] == 1,
                    f"index={body['current_question_index']}",
                )

                # 重复开始应被拒，避免误多生成一道题
                again = await client.post(
                    f"/api/interviews/{session_id}/start",
                    json={},
                )
                record(
                    "asking 状态下重复 start 返回 409",
                    again.status_code == 409,
                    f"status={again.status_code}",
                )

                # start 走过的中间态必须留下轨迹
                response = await client.get(
                    f"/api/interviews/{session_id}/history"
                )
                started_triggers = [
                    item["trigger"] for item in response.json()
                ]
                record(
                    "记录了 interview_started（两个中间步）",
                    started_triggers.count("interview_started") == 2,
                    f"count="
                    f"{started_triggers.count('interview_started')}",
                )
                record(
                    "记录了 question_generated",
                    "question_generated" in started_triggers,
                )

            # ========================================================
            # 4b. 暂停 / 恢复（在一个已经开始过的会话上）
            # ========================================================
            print()
            print("=" * 74)
            print("4b. 暂停 / 恢复")
            print("=" * 74)

            response = await client.post(
                f"/api/interviews/{session_id}/pause"
            )
            record(
                "asking 状态可暂停",
                response.status_code == 200,
                f"status={response.status_code}",
            )

            body = response.json()
            record(
                "暂停后状态为 paused",
                body.get("status") == "paused",
                f"status={body.get('status')}",
            )
            record(
                "记住恢复目标为 asking",
                body.get("resume_status") == "asking",
                f"resume_status={body.get('resume_status')}",
            )
            record(
                "记录了暂停时间",
                body.get("paused_at") is not None,
            )

            # 允许转移应包含恢复目标，且不该包含无关状态
            response = await client.get(
                f"/api/interviews/{session_id}/allowed-transitions"
            )
            allowed = response.json()
            record(
                "allowed-transitions 含 asking",
                "asking" in allowed,
                f"{allowed}",
            )

            response = await client.post(
                f"/api/interviews/{session_id}/resume"
            )
            record("恢复返回 200", response.status_code == 200)
            body = response.json()
            record(
                "恢复到 asking",
                body.get("status") == "asking",
                f"status={body.get('status')}",
            )
            record(
                "恢复后清空暂停信息",
                body.get("resume_status") is None
                and body.get("pause_reason") is None
                and body.get("paused_at") is None,
            )

            # 非 paused 会话恢复应报错
            response = await client.post(
                f"/api/interviews/{session_id}/resume"
            )
            record(
                "非 paused 会话恢复返回 409",
                response.status_code == 409,
                f"status={response.status_code}",
            )

            # draft 会话不允许暂停。此处会话是 asking，
            # 所以这次暂停应当成功 —— 用它验证"暂停后仍能继续"。
            response = await client.post(
                f"/api/interviews/{session_id}/pause"
            )
            record(
                "asking 会话可再次暂停",
                response.status_code == 200,
                f"status={response.status_code}",
            )
            # 恢复掉，继续后续流程
            await client.post(f"/api/interviews/{session_id}/resume")

            # ========================================================
            # 5. 题目列表
            # ========================================================
            print()
            print("=" * 74)
            print("5. 题目列表")
            print("=" * 74)

            response = await client.get(
                f"/api/interviews/{session_id}/questions"
            )
            questions = response.json()
            record(
                "题目列表可读取",
                len(questions) == 1,
                f"共 {len(questions)} 题",
            )
            if questions:
                record(
                    "is_general 与依据自洽",
                    questions[0]["is_general"]
                    == (not questions[0]["evidence_chunk_ids"]),
                )

            # ========================================================
            # 6. 提交作答
            # ========================================================
            print()
            print("=" * 74)
            print("6. 提交作答（调用 LLM，耗时较长）")
            print("=" * 74)

            # 不属于该会话的问题应被拒
            response = await client.post(
                f"/api/interviews/{session_id}/answer",
                json={"question_id": 999999, "answer": "越权的回答"},
            )
            record(
                "不属于该会话的题目返回 404",
                response.status_code == 404,
                f"status={response.status_code}",
            )

            if question is not None:
                response = await client.post(
                    f"/api/interviews/{session_id}/answer",
                    json={"question_id": question["id"], "answer": ""},
                )
                record(
                    "空回答被校验拦下（422）",
                    response.status_code == 422,
                    f"status={response.status_code}",
                )

                response = await client.post(
                    f"/api/interviews/{session_id}/answer",
                    json={
                        "question_id": question["id"],
                        "answer": ANSWER,
                    },
                )
                record(
                    "作答返回 200",
                    response.status_code == 200,
                    f"status={response.status_code}",
                )

                if response.status_code != 200:
                    print("      响应体:", response.text[:400])
                else:
                    body = response.json()
                    record(
                        "返回追问",
                        body.get("follow_up_question") is not None,
                    )
                    record(
                        "作答后状态回到 asking",
                        body.get("session_status") == "asking",
                        f"status={body.get('session_status')}",
                    )

                    follow_up = body["follow_up_question"]
                    record(
                        "追问继承原题依据",
                        follow_up["evidence_chunk_ids"]
                        == questions[0]["evidence_chunk_ids"],
                    )
                    record(
                        "分析摘要只含档位与缺口数，不含完整分析",
                        set(body["analysis_summary"].keys())
                        == {
                            "sample_count",
                            "missing_points_count",
                            "anchors",
                        },
                        f"keys={sorted(body['analysis_summary'].keys())}",
                    )
                    print(
                        f"      追问: {follow_up['question'][:52]}..."
                    )

            # ========================================================
            # 7. 详情与结束
            # ========================================================
            print()
            print("=" * 74)
            print("7. 详情与结束面试（调用 LLM）")
            print("=" * 74)

            response = await client.get(
                f"/api/interviews/{session_id}/detail"
            )
            record("详情返回 200", response.status_code == 200)
            detail = response.json()
            record(
                "详情含全部问答",
                len(detail["questions"]) >= 2,
                f"共 {len(detail['questions'])} 题",
            )
            record(
                "未评价时 evaluation 为 null",
                detail["evaluation"] is None,
            )
            record(
                "详情含 allowed_transitions",
                isinstance(detail["allowed_transitions"], list),
                f"{detail['allowed_transitions']}",
            )

            response = await client.post(
                f"/api/interviews/{session_id}/finish"
            )
            record(
                "结束面试返回 200",
                response.status_code == 200,
                f"status={response.status_code}",
            )

            if response.status_code != 200:
                print("      响应体:", response.text[:400])
            else:
                body = response.json()
                record(
                    "结束后状态为 completed",
                    body["session_status"] == "completed",
                    f"status={body['session_status']}",
                )
                evaluation = body["evaluation"]
                record(
                    "返回四项分数",
                    all(
                        isinstance(evaluation.get(key), int)
                        for key in (
                            "overall_score",
                            "technical_score",
                            "project_score",
                            "communication_score",
                        )
                    ),
                    f"{evaluation['overall_score']}/"
                    f"{evaluation['technical_score']}/"
                    f"{evaluation['project_score']}/"
                    f"{evaluation['communication_score']}",
                )
                record(
                    "返回结构化评价内容",
                    bool(evaluation["strengths"])
                    and bool(evaluation["weaknesses"])
                    and bool(evaluation["suggestions"]),
                    f"s={len(evaluation['strengths'])} "
                    f"w={len(evaluation['weaknesses'])} "
                    f"g={len(evaluation['suggestions'])}",
                )

            # completed 后不允许再作答
            if question is not None:
                response = await client.post(
                    f"/api/interviews/{session_id}/answer",
                    json={
                        "question_id": question["id"],
                        "answer": "终态作答",
                    },
                )
                record(
                    "completed 后作答返回 409",
                    response.status_code == 409,
                    f"status={response.status_code}",
                )

            # ========================================================
            # 8. 从 draft 直接开始（用户最常见的首次路径）
            # ========================================================
            print()
            print("=" * 74)
            print("8. 从 draft 直接开始（覆盖两步状态推进）")
            print("=" * 74)

            response = await client.post(
                "/api/interviews",
                json={
                    "project_id": PROJECT_ID,
                    "interview_type": "technical",
                    "target_role": "后端工程师（API 验证）",
                },
            )
            fresh = response.json()
            fresh_id = fresh["id"]
            record("新建会话为 draft", fresh["status"] == "draft")
            record(
                "新会话记录了所有者",
                fresh.get("user_id") == USER_ID,
            )

            # 不传 query：验证服务端会退化为 target_role
            response = await client.post(
                f"/api/interviews/{fresh_id}/start",
                json={},
            )
            record(
                "draft 状态可直接开始（200）",
                response.status_code == 200,
                f"status={response.status_code}",
            )

            if response.status_code != 200:
                print("      响应体:", response.text[:400])
            else:
                fresh_body = response.json()
                record(
                    "draft → asking 一步到位",
                    fresh_body["session_status"] == "asking",
                    f"status={fresh_body['session_status']}",
                )
                record(
                    "生成了首题",
                    bool(fresh_body["question"]["question"]),
                )
                record(
                    "索引为 1",
                    fresh_body["current_question_index"] == 1,
                )

            # 暂停在 asking 的会话
            response = await client.post(
                f"/api/interviews/{fresh_id}/pause"
            )
            record(
                "asking 状态可暂停",
                response.status_code == 200,
            )
            record(
                "恢复目标为 asking",
                response.json().get("resume_status") == "asking",
                f"resume={response.json().get('resume_status')}",
            )

            # ========================================================
            # 9. 轨迹完整性
            # ========================================================
            print()
            print("=" * 74)
            print("9. 状态轨迹")
            print("=" * 74)

            response = await client.get(
                f"/api/interviews/{session_id}/history"
            )
            history = response.json()
            triggers = {item["trigger"] for item in history}

            record(
                "轨迹记录了开始与生成首题",
                {
                    "interview_started",
                    "question_generated",
                }
                <= triggers,
                f"共 {len(history)} 条",
            )
            record(
                "轨迹记录了评价完成",
                "evaluation_completed" in triggers,
            )
            record(
                "轨迹末条为 completed",
                history and history[-1]["to_status"] == "completed",
                f"最后={history[-1]['to_status'] if history else None}",
            )
            record(
                "轨迹没有自转移",
                all(
                    item["from_status"] != item["to_status"]
                    for item in history
                ),
            )
            record(
                "轨迹首尾相接（无缺口）",
                all(
                    history[i]["from_status"]
                    == history[i - 1]["to_status"]
                    for i in range(1, len(history))
                ),
            )

        finally:
            # 无论断言是否失败都必须清理：残留会话与配额会干扰
            # 下一次运行，而且让失败结论指向错误的地方（D38）。
            async with AsyncSessionLocal() as db:
                for target in (session_id, fresh_id):
                    if target is None:
                        continue
                    await db.execute(
                        text(
                            "DELETE FROM interview_sessions "
                            "WHERE id = :sid"
                        ),
                        {"sid": target},
                    )
                await db.commit()

            print(f"已清理验证会话 {session_id} 与 {fresh_id}")

            # 恢复默认配额，避免影响后续脚本
            await reset_quota(USER_ID)

    # ========================================================
    # 汇总
    # ========================================================
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
