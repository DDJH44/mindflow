"""验证面试成本预估（纯逻辑 + 一条端到端）。

## 它防的是什么

原界面只回答"**能不能开始**"（`allowed`），而没有回答
"**能不能开完**"。这两者不同，而后者才会让用户白损失一场：

> 剩余 2 场、8 题时把题目上限设成 8 并开始面试 ——
> 第 3 场能开，但**第 9 题会被拒**，面试在中间断掉。
> 而那时场次已经花掉了，用户既没得到完整面试，也拿不回场次。

因此需要提前告知。而"提前告知"的正确性依赖两个事实：

1. 消耗上界是**确定的**（`max_questions`，含追问）
2. 判定用的是**规范化之后**的预算（越界值被夹取）

第 2 条特别容易错：若预估用原始值而实际执行用夹取后的值，
预估就会说"需要 9999 题"—— 而那是错的。

## 它验证六件事

1. 够开完 → `fits=True`
2. 题目额度不足 → `blocked_by=insufficient_questions`
   且 `truncates_at` 给出**会断在第几题**
3. 场次用完 → `blocked_by=insufficient_interviews`
4. 越界预算被**夹取**（与真正执行时同一个规范化函数）
5. 场次不足优先于题目不足（"开不了"比"开不完"更早发生）
6. **端到端**：`GET /api/usage` 真的返回 `cost_preview`

用法：uv run python -m tests.test_interview_cost
"""

import asyncio
import sys

import httpx
from sqlalchemy import text

from app.database.session import AsyncSessionLocal


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


async def main() -> int:
    sys.path.insert(0, r"D:\yuxi\MindFlow\backend")

    from app.services.interview.interview_termination import (
        DEFAULT_MAX_QUESTIONS,
        MAX_ALLOWED_QUESTIONS,
    )
    from app.services.usage.interview_cost import estimate_cost

    result = Result()

    print("=" * 78)
    print("面试成本预估验证")
    print("=" * 78)

    # ================================================
    print()
    print("=" * 78)
    print("① 额度充足 → 能开完")
    print("=" * 78)

    # ================================================
    estimate = estimate_cost(
        max_questions=8,
        interviews_remaining=2,
        questions_remaining=20,
    )

    result.check(
        "fits=True",
        estimate.fits is True,
        f"questions={estimate.questions}",
    )
    result.check(
        "没有 blocked_by",
        estimate.blocked_by is None,
    )
    result.check(
        "truncates_at 为 None（不会断）",
        estimate.truncates_at is None,
    )

    # ================================================
    print()
    print("=" * 78)
    print("② 题目额度不足 → 能开但会断（最需要提前告知的一种）")
    print("=" * 78)

    estimate = estimate_cost(
        max_questions=8,
        interviews_remaining=2,
        questions_remaining=5,
    )

    result.check(
        "fits=False",
        estimate.fits is False,
    )
    result.check(
        "blocked_by = insufficient_questions",
        estimate.blocked_by == "insufficient_questions",
        f"实得 {estimate.blocked_by!r}",
    )
    result.check(
        "truncates_at 指出断在第几题",
        estimate.truncates_at == 5,
        f"实得 {estimate.truncates_at}",
    )

    # 文案必须说明"会断"，而不是只说"额度不够"。
    # 因为用户最需要知道的是**后果**，不是原因。
    message = estimate.describe(5)

    print(f"      文案：{message}")

    result.check(
        "文案给出了具体数字（不是'额度不足'这种空话）",
        "5" in message,
        "含剩余题数",
    )
    result.check(
        "文案说明了后果（会中途中断）",
        "中断" in message or "断" in message,
    )

    # ================================================
    print()
    print("=" * 78)
    print("③ 场次用完 → 开不了")
    print("=" * 78)

    estimate = estimate_cost(
        max_questions=8,
        interviews_remaining=0,
        questions_remaining=50,
    )

    result.check(
        "blocked_by = insufficient_interviews",
        estimate.blocked_by == "insufficient_interviews",
        f"实得 {estimate.blocked_by!r}",
    )

    # ================================================
    print()
    print("=" * 78)
    print("④ 越界预算被夹取（与真正执行时同一函数）")
    print("=" * 78)

    # 这一条防的是"预估用原始值、执行用夹取值"的不一致：
    # 若不夹取，预估会说"需要 9999 题"，而实际最多只问 30 题。
    estimate = estimate_cost(
        max_questions=9999,
        interviews_remaining=5,
        questions_remaining=200,
    )

    result.check(
        f"9999 被夹取为 {MAX_ALLOWED_QUESTIONS}",
        estimate.questions == MAX_ALLOWED_QUESTIONS,
        f"实得 {estimate.questions}",
    )

    default_estimate = estimate_cost(
        max_questions=None,
        interviews_remaining=5,
        questions_remaining=200,
    )

    result.check(
        f"None 回退到默认 {DEFAULT_MAX_QUESTIONS}",
        default_estimate.questions == DEFAULT_MAX_QUESTIONS,
        f"实得 {default_estimate.questions}",
    )

    # ================================================
    print()
    print("=" * 78)
    print("⑤ 场次不足优先于题目不足")
    print("=" * 78)

    # 判据："开不了"比"开不完"更早发生 ——
    # 一个开不了的面试，题目额度够不够没有意义。
    estimate = estimate_cost(
        max_questions=8,
        interviews_remaining=0,
        questions_remaining=0,
    )

    result.check(
        "两者都不足时，报 insufficient_interviews",
        estimate.blocked_by == "insufficient_interviews",
        f"实得 {estimate.blocked_by!r}",
    )

    # ================================================
    print()
    print("=" * 78)
    print("⑥ 端到端：GET /api/usage 返回 cost_preview")
    print("=" * 78)

    async with AsyncSessionLocal() as db:
        user_id = (
            await db.execute(
                text("SELECT id FROM users WHERE username=:u"),
                {"u": "mindflow"},
            )
        ).scalar_one()

        original_quota = (
            await db.execute(
                text(
                    "SELECT monthly_question_quota FROM users WHERE id=:i"
                ),
                {"i": user_id},
            )
        ).scalar_one()

        # 压到 5 题，制造"剩 5 题"的场景
        await db.execute(
            text(
                "UPDATE users SET monthly_question_quota=5 WHERE id=:i"
            ),
            {"i": user_id},
        )
        await db.execute(
            text("DELETE FROM interview_usage WHERE user_id=:i"),
            {"i": user_id},
        )
        await db.commit()

    from app.core.security import create_access_token

    token = create_access_token({"sub": str(user_id)})

    try:
        async with httpx.AsyncClient(
            base_url="http://127.0.0.1:5173/api",
            headers={"Authorization": f"Bearer {token}"},
            timeout=60.0,
        ) as client:
            # 设 3 题：够
            response = await client.get(
                "/usage", params={"max_questions": 3}
            )
            preview = response.json().get("cost_preview") or {}

            result.check(
                "剩 5 题、设 3 题 → fits=True",
                preview.get("fits") is True,
                f"fits={preview.get('fits')}",
            )

            # 设 8 题：不够
            response = await client.get(
                "/usage", params={"max_questions": 8}
            )
            preview = response.json().get("cost_preview") or {}

            result.check(
                "剩 5 题、设 8 题 → fits=False",
                preview.get("fits") is False,
                f"fits={preview.get('fits')}",
            )
            result.check(
                "blocked_by 指出是题目额度",
                preview.get("blocked_by") == "insufficient_questions",
                f"实得 {preview.get('blocked_by')!r}",
            )
            result.check(
                "truncates_at=5",
                preview.get("truncates_at") == 5,
                f"实得 {preview.get('truncates_at')}",
            )

            print(f"      文案：{preview.get('message', '')}")

    finally:
        async with AsyncSessionLocal() as db:
            await db.execute(
                text(
                    "UPDATE users SET monthly_question_quota=:q "
                    "WHERE id=:i"
                ),
                {"q": original_quota, "i": user_id},
            )
            await db.execute(
                text("DELETE FROM interview_usage WHERE user_id=:i"),
                {"i": user_id},
            )
            await db.commit()

        print(f"      已恢复题目额度为 {original_quota}")

    # ================================================
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
