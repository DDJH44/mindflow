"""验证逐轮分析被留档并能在复盘时读回。

## 它防的是什么

每轮作答都会做一次分析（`InterviewAnswerAnalyzer`），产出
`missing_points`、三个维度的档位、优点与建议。

而这份分析**此前算完就丢**：只用来生成追问，然后随请求结束消失。
后果是"面试结束后也无从复盘这一题我漏了什么" ——
那正是面试练习工具的核心价值。

## 它验证四件事

1. `save_analysis` 真的把快照写进库（不是只返回 True）
2. 快照只含**复盘需要的字段**（不把内部实现细节写进历史记录）
3. `get_questions_with_answers` 会把快照暴露出去
4. 没有快照的历史回答**不报错**（`None`，不是异常）

第 3 条是端到端的关键：数据存了但没暴露，对用户等于没存。

用法：uv run python -m tests.test_answer_analysis_persisted
"""

import asyncio
import sys
from datetime import datetime

from sqlalchemy import text

from app.database.session import AsyncSessionLocal

TEST_PROJECT_NAME = "__test__analysis-persist"


class FakeAnalysis:
    """`AnswerAnalysis` 的最小替身。

    用替身而不是真的调模型：本测试要验证的是**存储与读取**，
    不是分析质量。依赖真实模型会让它变慢且不稳定。
    """

    def __init__(self) -> None:
        self.missing_points = ["chunk_id", "一致性", "失败处理"]
        self.sample_count = 1

    def anchor_summary(self) -> dict:
        return {
            "answer_quality": {"anchor": "C", "label": "偏弱"},
            "technical_depth": {"anchor": "D", "label": "很差"},
            "completeness": {"anchor": "B", "label": "良好"},
        }


class Result:
    def __init__(self) -> None:
        self.rows: list[tuple[str, bool, str]] = []

    def check(self, label: str, ok: bool, detail: str = "") -> None:
        self.rows.append((label, ok, detail))
        mark = "OK  " if ok else "FAIL"
        suffix = f"  {detail}" if detail else ""
        print(f"  [{mark}] {label}{suffix}")

    @property
    def failed(self) -> list[str]:
        return [label for label, ok, _ in self.rows if not ok]


async def cleanup() -> None:
    async with AsyncSessionLocal() as db:
        await db.execute(
            text(
                "DELETE FROM projects WHERE name = :n"
            ),
            {"n": TEST_PROJECT_NAME},
        )
        await db.commit()


async def prepare() -> tuple[int, int, int]:
    """建项目 + 会话 + 题目，返回 (project_id, session_id, question_id)。"""

    async with AsyncSessionLocal() as db:
        user_id = (
            await db.execute(
                text("SELECT id FROM users WHERE username=:u"),
                {"u": "mindflow"},
            )
        ).scalar_one()

        project_id = (
            await db.execute(
                text(
                    "INSERT INTO projects (name, owner_id, status, "
                    "created_at, updated_at) "
                    "VALUES (:n, :u, 'active', now(), now()) "
                    "RETURNING id"
                ),
                {"n": TEST_PROJECT_NAME, "u": user_id},
            )
        ).scalar_one()

        session_id = (
            await db.execute(
                text(
                    "INSERT INTO interview_sessions ("
                    "project_id, user_id, status, interview_type, "
                    "current_question_index, max_questions, "
                    "questions_asked, created_at, updated_at) "
                    "VALUES (:p, :u, 'completed', 'mixed', 0, 8, 1, "
                    "now(), now()) RETURNING id"
                ),
                {"p": project_id, "u": user_id},
            )
        ).scalar_one()

        question_id = (
            await db.execute(
                text(
                    "INSERT INTO interview_questions ("
                    "session_id, question, question_type, "
                    "question_index, is_general, created_at) "
                    "VALUES (:s, '测试题目', 'project', 0, false, now()) "
                    "RETURNING id"
                ),
                {"s": session_id},
            )
        ).scalar_one()

        await db.commit()

    return project_id, session_id, question_id


async def main() -> int:
    sys.path.insert(0, r"D:\yuxi\MindFlow\backend")

    from app.services.interview_answer_service import (
        InterviewAnswerService,
    )
    from app.services.interview.interview_flow_service import (
        InterviewFlowService,
    )

    result = Result()

    print("=" * 78)
    print("逐轮分析留档验证")
    print("=" * 78)

    await cleanup()

    project_id = session_id = question_id = 0

    try:
        project_id, session_id, question_id = await prepare()

        async with AsyncSessionLocal() as db:
            answer_service = InterviewAnswerService(db)

            answer = await answer_service.create_answer(
                question_id=question_id,
                answer="这是一段测试回答。",
            )

        # ================================================
        print()
        print("=" * 78)
        print("① 保存快照")
        print("=" * 78)

        async with AsyncSessionLocal() as db:
            ok = await InterviewAnswerService(db).save_analysis(
                answer_id=answer.id,
                analysis=FakeAnalysis(),
            )

        result.check("save_analysis 返回 True", ok)

        # 直接查库 —— 不能只信返回值。
        # "返回 True 但没写进去"是典型的假通过。
        async with AsyncSessionLocal() as db:
            snapshot = (
                await db.execute(
                    text(
                        "SELECT analysis_snapshot FROM interview_answers "
                        "WHERE id = :i"
                    ),
                    {"i": answer.id},
                )
            ).scalar_one()

        result.check(
            "库里确实写入了快照",
            snapshot is not None,
            f"{type(snapshot).__name__}",
        )

        # ================================================
        print()
        print("=" * 78)
        print("② 快照只含复盘需要的字段")
        print("=" * 78)

        if isinstance(snapshot, dict):
            keys = set(snapshot.keys())

            print(f"      字段：{sorted(keys)}")

            result.check(
                "含 missing_points",
                "missing_points" in keys,
            )
            result.check(
                "含 anchors",
                "anchors" in keys,
            )
            result.check(
                "missing_points 内容完整",
                snapshot.get("missing_points")
                == ["chunk_id", "一致性", "失败处理"],
                f"{snapshot.get('missing_points')}",
            )
            result.check(
                "anchors 是 {维度: 档位} 而不是嵌套结构",
                snapshot.get("anchors", {}).get("technical_depth")
                == "D",
                f"{snapshot.get('anchors')}",
            )

            # 刻意不存原始提示词/模型输出：体积大且无复盘价值。
            result.check(
                "没有把内部实现细节写进历史记录",
                not keys & {"prompt", "raw", "model_output", "system_prompt"},
                f"{sorted(keys)}",
            )
        else:
            result.check("快照是 dict", False, f"实得 {type(snapshot)}")

        # ================================================
        print()
        print("=" * 78)
        print("③ 复盘时能读回（存了但没暴露 = 对用户等于没存）")
        print("=" * 78)

        async with AsyncSessionLocal() as db:
            items = await InterviewFlowService(
                db
            ).get_questions_with_answers(session_id=session_id)

        result.check(
            "返回了一道题",
            len(items) == 1,
            f"{len(items)} 道",
        )

        exposed = items[0].get("analysis") if items else None

        result.check(
            "题目详情里带出了 analysis",
            isinstance(exposed, dict),
            f"{type(exposed).__name__}",
        )

        result.check(
            "暴露的缺口与库里一致",
            isinstance(exposed, dict)
            and exposed.get("missing_points")
            == ["chunk_id", "一致性", "失败处理"],
            f"{exposed.get('missing_points') if exposed else None}",
        )

        # ================================================
        print()
        print("=" * 78)
        print("④ 没有快照的历史回答不报错")
        print("=" * 78)

        # 历史数据（本功能之前）没有快照。读取时必须给 None，
        # 而不是抛异常或伪造空结构 —— 后者会让界面显示
        # "0 个缺口"，那是在说谎。
        async with AsyncSessionLocal() as db:
            second_question = (
                await db.execute(
                    text(
                        "INSERT INTO interview_questions ("
                        "session_id, question, question_type, "
                        "question_index, is_general, created_at) "
                        "VALUES (:s, '第二题', 'project', 1, false, now()) "
                        "RETURNING id"
                    ),
                    {"s": session_id},
                )
            ).scalar_one()

            await db.execute(
                text(
                    "INSERT INTO interview_answers ("
                    "question_id, answer, created_at) "
                    "VALUES (:q, '历史回答', now())"
                ),
                {"q": second_question},
            )
            await db.commit()

        async with AsyncSessionLocal() as db:
            items = await InterviewFlowService(
                db
            ).get_questions_with_answers(session_id=session_id)

        legacy = [i for i in items if i["answer"] == "历史回答"]

        result.check(
            "历史回答的 analysis 是 None（不是空 dict）",
            len(legacy) == 1 and legacy[0].get("analysis") is None,
            f"{legacy[0].get('analysis') if legacy else '未找到'}",
        )

    finally:
        await cleanup()
        print()
        print("  已清理测试数据")

    print()
    print("=" * 78)
    passed = len(result.rows) - len(result.failed)
    print(f"通过 {passed} / {len(result.rows)}")

    if result.failed:
        print("失败项:")
        for label in result.failed:
            print(f"  - {label}")
        return 1

    print("全部通过")
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
