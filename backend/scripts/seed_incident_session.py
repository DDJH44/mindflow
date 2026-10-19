"""造一条"只答一题且回答为不知道"的会话并触发评价（§38 验证用）。

## 为什么要造

`communication` 判档公平性的修复，最终要在**真实链路**上见效：
会话 → 结束 → 评价落库 → 报告页显示样本量提示。

只在单元层面验证提示词是不够的 —— 那只能证明"提示词里有那句话"，
证明不了"界面上真的显示了"。

## 它做什么

1. 在项目 3 下建一条会话（1 题，已答"不知道"）
2. 直接调用 `EvaluationService.evaluate_session` 触发真实评价
3. 打印四维分数与锚点

跑完会打印会话 id，供截图使用：

    uv run python -m scripts.seed_incident_session
    uv run python -m tests.capture_screens --report-id <id>

✅ 这条会话完成后可用 `cleanup_test_sessions.py` 清理吗？
**不能** —— 它有作答记录，而那个工具的判据是"0 题 0 答"。
因此脚本会打印精确的删除语句，由人决定。
"""

import asyncio
import sys

from sqlalchemy import text

from app.database.session import AsyncSessionLocal

ACCOUNT = "mindflow"
PROJECT_ID = 3

# 与用户实际遇到的那次同型：一个具体的技术追问 + 回答"不知道"。
QUESTION = (
    "你在项目中提到基于 WebSocket 实现群聊消息推送，"
    "并通过 conversationId / artifactId 绑定来解决多会话产物串台问题。"
    "请具体描述一下你是如何设计数据模型的？"
)
ANSWER = "不知道"


async def main() -> int:
    sys.path.insert(0, r"D:\yuxi\MindFlow\backend")

    from app.services.interview.evaluation_service import (
        InterviewEvaluationService,
    )

    async with AsyncSessionLocal() as db:
        user_id = (
            await db.execute(
                text("SELECT id FROM users WHERE username=:u"),
                {"u": ACCOUNT},
            )
        ).scalar_one()

        session_id = (
            await db.execute(
                text(
                    """
                    INSERT INTO interview_sessions (
                        project_id, user_id, status, interview_type,
                        target_role, current_question_index,
                        max_questions, questions_asked,
                        termination_reason, created_at, updated_at
                    )
                    VALUES (
                        :p, :u, 'evaluating', 'mixed', '后端工程师', 0,
                        1, 1, 'budget_exhausted', now(), now()
                    )
                    RETURNING id
                    """
                ),
                {"p": PROJECT_ID, "u": user_id},
            )
        ).scalar_one()

        question_id = (
            await db.execute(
                text(
                    """
                    INSERT INTO interview_questions (
                        session_id, question, question_type,
                        question_index, is_general, created_at
                    )
                    VALUES (:s, :q, 'project', 0, false, now())
                    RETURNING id
                    """
                ),
                {"s": session_id, "q": QUESTION},
            )
        ).scalar_one()

        await db.execute(
            text(
                """
                INSERT INTO interview_answers (
                    question_id, answer, created_at
                )
                VALUES (:q, :a, now())
                """
            ),
            {"q": question_id, "a": ANSWER},
        )

        await db.commit()

    print()
    print(f"  已创建会话 id={session_id}（1 题，回答「{ANSWER}」）")
    print("  正在触发真实评价（会调用模型，约 30-60 秒）…")

    async with AsyncSessionLocal() as db:
        evaluation = await InterviewEvaluationService(db).evaluate_session(
            session_id
        )

    print()
    print("  评价结果：")
    print(f"    综合 {evaluation.get('overall_score')}")
    print(f"    技术 {evaluation.get('technical_score')}")
    print(f"    项目 {evaluation.get('project_score')}")
    print(f"    沟通 {evaluation.get('communication_score')}")
    print()
    print("  锚点：")

    try:
        details = evaluation.get("scoring_details") or {}
        anchors = details.get("anchors", {})

        for name, item in anchors.items():
            if name == "anchor_reason":
                print(f"    reason: {item}")
                continue

            print(
                f"    {name:<14} {item.get('anchor')} "
                f"({item.get('label')}) → {item.get('score')}"
            )
    except Exception as exc:  # noqa: BLE001
        print(f"    （解析锚点失败：{exc}）")

    print()
    print(f"  截图：uv run python -m tests.capture_screens "
          f"--report-id {session_id}")
    print(f"  清理：DELETE FROM interview_sessions WHERE id = {session_id};")

    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
