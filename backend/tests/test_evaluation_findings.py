"""验证可追溯结论（findings）：出处校验、聚合、落库。

## 它防的是什么

报告里的优势/不足/建议此前**没有出处** —— 用户问"你凭什么说我
技术深度不够"，界面答不出来。现在让模型标注依据第几轮，本地校验。

而"可追溯"这个功能有一个特别危险的失败模式：**看起来可追溯，
实际指错了地方。** 用户点过去看到另一道题，会认为整份报告不可信。
因此本套件重点验证"宁可没有出处，也不给错出处"：

- 模型改写了原文 → 丢弃（否则 UI 匹配不上，只能靠模糊匹配，
  而模糊匹配会把出处挂到错误的条目上）
- 轮次越界（第 5 轮不存在）→ 丢弃，不夹取
- 轮次是 `True` / `"第2轮"` / `0` → 丢弃
- 多次采样的出处**取交集**（两次指的轮次不同 = 它们自己都没把握）

## 它验证

1. 逐条校验规则
2. 多采样聚合时出处取交集
3. 落库往返：`findings` 存得进、读得出；历史记录为 `None`

用法：uv run python -m tests.test_evaluation_findings
"""

import asyncio
import sys

from sqlalchemy import text

from app.database.session import AsyncSessionLocal

TEST_PROJECT_NAME = "__test__findings"


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


STRENGTH = "第一轮能清晰陈述数据处理链路"
WEAKNESS = "技术深度不足：未说明分块参数依据"
SUGGESTION = "回答技术问题时补上参数取舍理由"


def make_evaluation(findings: list) -> object:
    from app.schemas.interview.evaluation import (
        InterviewEvaluationResult,
    )

    return InterviewEvaluationResult(
        overall_anchor="C",
        technical_anchor="C",
        project_anchor="C",
        communication_anchor="B",
        anchor_reason="测试",
        strengths=[STRENGTH],
        weaknesses=[WEAKNESS],
        suggestions=[SUGGESTION],
        feedback="测试反馈",
        findings=findings,
    )


def check_validation(result: Result) -> None:
    from app.schemas.interview.evaluation import (
        InterviewEvaluationResult,
        Finding,
    )
    from app.services.interview.evaluator import _validate_findings

    print()
    print("=" * 78)
    print("① 原样引用才保留")
    print("=" * 78)

    evaluation = make_evaluation(
        [
            # 原样引用 + 合法轮次
            Finding(
                kind="weakness",
                text=WEAKNESS,
                turns=[1],
            ),
            # 改写了两个字 → 必须丢弃
            Finding(
                kind="weakness",
                text="技术深度不足：未说明分块参数的依据",
                turns=[1],
            ),
            # kind 与原文所属分组不符（把优势写成 weakness）→ 丢弃
            Finding(
                kind="weakness",
                text=STRENGTH,
                turns=[1],
            ),
        ]
    )

    cleaned = _validate_findings(evaluation, turn_count=3)

    result.check(
        "只保留原样引用的那一条",
        len(cleaned) == 1,
        f"{len(cleaned)} 条",
    )
    result.check(
        "保留的是原文那条",
        cleaned and cleaned[0].text == WEAKNESS,
    )
    result.check(
        "合法出处的 verified 为 True",
        cleaned and cleaned[0].verified is True,
    )

    # ================================================
    print()
    print("=" * 78)
    print("② 轮次非法 → 丢弃该轮次（不夹取）")
    print("=" * 78)

    evaluation = make_evaluation(
        [
            Finding(
                kind="weakness",
                text=WEAKNESS,
                # 99 不存在；0 不是合法轮次；True 是 int 子类，必须排除；
                # "第2轮" 不是整数。只有 2 合法。
                turns=[99, 0, True, "第2轮", 2],
            )
        ]
    )

    cleaned = _validate_findings(evaluation, turn_count=3)

    result.check(
        "只剩合法轮次 [2]",
        cleaned and cleaned[0].turns == [2],
        f"{cleaned[0].turns if cleaned else None}",
    )

    # 全部轮次非法 → 结论保留但 verified=False。
    # 保留结论很重要：它是模型给出的评价内容，不该因为没标好出处就消失。
    evaluation = make_evaluation(
        [Finding(kind="weakness", text=WEAKNESS, turns=[99])]
    )

    cleaned = _validate_findings(evaluation, turn_count=3)

    result.check(
        "轮次全非法 → 结论保留但 verified=False",
        len(cleaned) == 1 and cleaned[0].verified is False,
        f"turns={cleaned[0].turns if cleaned else None}",
    )

    # ================================================
    print()
    print("=" * 78)
    print("③ 多次采样：出处取交集")
    print("=" * 78)

    from app.services.interview.evaluator import _aggregate_samples

    first = make_evaluation(
        [Finding(kind="weakness", text=WEAKNESS, turns=[1, 2])]
    )
    # 第二次采样：同一结论但只指第 2 轮
    second = make_evaluation(
        [Finding(kind="weakness", text=WEAKNESS, turns=[2, 3])]
    )

    # `_aggregate_samples` 期望入参已经过校验（`evaluate` 里就是这么做的）
    first.findings = _validate_findings(first, turn_count=3)
    second.findings = _validate_findings(second, turn_count=3)

    merged = _aggregate_samples([first, second])

    result.check(
        "同一结论合并成一条",
        len(merged.findings) == 1,
        f"{len(merged.findings)} 条",
    )
    result.check(
        "出处取交集（两次都指的只有 2）",
        merged.findings and merged.findings[0].turns == [2],
        f"{merged.findings[0].turns if merged.findings else None}",
    )

    # 交集为空 → 保留结论、标为未验证（两次自己都没把握）
    first = make_evaluation(
        [Finding(kind="weakness", text=WEAKNESS, turns=[1])]
    )
    second = make_evaluation(
        [Finding(kind="weakness", text=WEAKNESS, turns=[3])]
    )
    first.findings = _validate_findings(first, turn_count=3)
    second.findings = _validate_findings(second, turn_count=3)

    merged = _aggregate_samples([first, second])

    result.check(
        "交集为空 → 结论保留、verified=False",
        len(merged.findings) == 1
        and merged.findings[0].verified is False,
        f"{merged.findings[0].turns if merged.findings else None}",
    )


async def check_roundtrip(result: Result) -> None:
    """落库往返：存得进、读得出。"""

    from app.repositories.interview_evaluation_repository import (
        InterviewEvaluationRepository,
    )

    print()
    print("=" * 78)
    print("④ 落库往返")
    print("=" * 78)

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
                    "VALUES (:p, :u, 'completed', 'mixed', 0, 4, 2, "
                    "now(), now()) RETURNING id"
                ),
                {"p": project_id, "u": user_id},
            )
        ).scalar_one()

        # 第二个会话：用于验证"未传 findings 时落库为 NULL"。
        # 必须真的建一行 —— 拿 `session_id + 1000000` 会触发外键约束
        # （本项目第一次写这个测试时就踩了）。
        legacy_session_id = (
            await db.execute(
                text(
                    "INSERT INTO interview_sessions ("
                    "project_id, user_id, status, interview_type, "
                    "current_question_index, max_questions, "
                    "questions_asked, created_at, updated_at) "
                    "VALUES (:p, :u, 'completed', 'mixed', 0, 4, 2, "
                    "now(), now()) RETURNING id"
                ),
                {"p": project_id, "u": user_id},
            )
        ).scalar_one()

        await db.commit()

    payload = [
        {
            "kind": "weakness",
            "text": WEAKNESS,
            "turns": [1],
            "verified": True,
        }
    ]

    async with AsyncSessionLocal() as db:
        await InterviewEvaluationRepository(db).create(
            session_id=session_id,
            overall_score=40,
            technical_score=40,
            project_score=40,
            communication_score=70,
            feedback="测试",
            strengths=[STRENGTH],
            weaknesses=[WEAKNESS],
            suggestions=[SUGGESTION],
            findings=payload,
        )

    async with AsyncSessionLocal() as db:
        row = (
            await db.execute(
                text(
                    "SELECT findings FROM interview_evaluations "
                    "WHERE session_id = :s"
                ),
                {"s": session_id},
            )
        ).scalar_one()

    result.check(
        "findings 已落库",
        isinstance(row, list) and len(row) == 1,
        f"{row}",
    )
    result.check(
        "轮次与验证标记都保留",
        isinstance(row, list)
        and row[0].get("turns") == [1]
        and row[0].get("verified") is True,
    )

    # 不传 findings 时必须是 NULL，而不是 [] ——
    # 两者在 UI 上含义不同：NULL 隐藏整块，[] 表示"有这功能但没内容"。
    async with AsyncSessionLocal() as db:
        await InterviewEvaluationRepository(db).create(
            session_id=legacy_session_id,
            overall_score=40,
            technical_score=40,
            project_score=40,
            communication_score=70,
            feedback="测试2",
        )

    async with AsyncSessionLocal() as db:
        legacy = (
            await db.execute(
                text(
                    "SELECT findings FROM interview_evaluations "
                    "WHERE session_id = :s"
                ),
                {"s": legacy_session_id},
            )
        ).scalar_one_or_none()

    result.check(
        "未传 findings 时落库为 NULL（不写 [])",
        legacy is None,
        f"{legacy!r}",
    )


async def cleanup() -> None:
    async with AsyncSessionLocal() as db:
        await db.execute(
            text(
                "DELETE FROM interview_evaluations WHERE session_id IN "
                "(SELECT id FROM interview_sessions "
                "WHERE project_id IN (SELECT id FROM projects "
                "WHERE name = :n))"
            ),
            {"n": TEST_PROJECT_NAME},
        )
        await db.execute(
            text("DELETE FROM projects WHERE name = :n"),
            {"n": TEST_PROJECT_NAME},
        )
        await db.commit()


async def main() -> int:
    sys.path.insert(0, r"D:\yuxi\MindFlow\backend")

    result = Result()

    print("=" * 78)
    print("可追溯结论验证")
    print("=" * 78)

    check_validation(result)

    await cleanup()

    try:
        await check_roundtrip(result)
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
