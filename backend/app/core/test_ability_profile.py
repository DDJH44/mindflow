"""验证能力画像端点。

覆盖两类样本：
- **样本充足**（≥3 场）：验证中位数 / 区间 / 极差 / 逐次趋势 / 重复弱点
- **样本不足**（<3 场）：验证**不下结论**并给出说明

为什么必须覆盖后者：只有 1–2 场时分数差异完全可能来自采样波动
（§8A.9 实测极差 10–20 分）。此时若照样给"优势 / 短板"，
就是在编造用户看不出破绽的结论。

用法：uv run python -m app.core.test_ability_profile
"""

import asyncio
import json
import sys
import uuid

import httpx
from sqlalchemy import text

from app.database.session import AsyncSessionLocal

BASE = "http://127.0.0.1:5173/api"
ACCOUNT = "mindflow"

results: list[tuple[str, bool]] = []


def record(label: str, ok: bool, extra: str = "") -> None:
    results.append((label, ok))
    suffix = f"  {extra}" if extra else ""
    print(f"  [{'OK  ' if ok else 'FAIL'}] {label}{suffix}")


async def seed_evaluation(
    user_id: int,
    project_id: int,
    scores: dict[str, int],
    weaknesses: list[str],
    suggestions: list[str],
    target_role: str | None = None,
) -> int:
    """造一场已完成面试 + 一条评价，返回 session_id。"""

    async with AsyncSessionLocal() as db:
        session_id = (
            await db.execute(
                text(
                    "INSERT INTO interview_sessions "
                    "(project_id, user_id, status, interview_type, "
                    " target_role, current_question_index, "
                    " max_questions, questions_asked, "
                    " termination_reason, created_at, updated_at) "
                    "VALUES (:p, :u, 'completed', 'technical', "
                    " :role, 2, 2, 2, 'budget_exhausted', "
                    " now(), now()) "
                    "RETURNING id"
                ),
                {
                    "p": project_id,
                    "u": user_id,
                    "role": target_role,
                },
            )
        ).scalar_one()

        await db.execute(
            text(
                "INSERT INTO interview_evaluations "
                "(session_id, overall_score, technical_score, "
                " project_score, communication_score, feedback, "
                " strengths, weaknesses, suggestions, "
                " scoring_details, created_at) "
                "VALUES (:s, :o, :t, :p, :c, '验证用反馈', "
                " CAST(:st AS JSON), CAST(:wk AS JSON), "
                " CAST(:sg AS JSON), CAST(:sd AS JSON), now())"
            ),
            {
                "s": session_id,
                "o": scores["overall_score"],
                "t": scores["technical_score"],
                "p": scores["project_score"],
                "c": scores["communication_score"],
                "st": json.dumps([]),
                "wk": json.dumps(weaknesses),
                "sg": json.dumps(suggestions),
                "sd": json.dumps({}),
            },
        )
        await db.commit()

    return session_id


async def cleanup(session_ids: list[int]) -> None:
    if not session_ids:
        return

    async with AsyncSessionLocal() as db:
        await db.execute(
            text(
                "DELETE FROM interview_evaluations "
                "WHERE session_id = ANY(:s)"
            ),
            {"s": session_ids},
        )
        await db.execute(
            text(
                "DELETE FROM interview_status_history "
                "WHERE session_id = ANY(:s)"
            ),
            {"s": session_ids},
        )
        await db.execute(
            text("DELETE FROM interview_sessions WHERE id = ANY(:s)"),
            {"s": session_ids},
        )
        await db.commit()


async def main() -> int:
    sys.path.insert(0, r"D:\yuxi\MindFlow\backend")

    from app.core.security import create_access_token

    async with AsyncSessionLocal() as db:
        user_id = (
            await db.execute(
                text("SELECT id FROM users WHERE username = :u"),
                {"u": ACCOUNT},
            )
        ).scalar_one()

        # 用一个**新建的干净项目**隔离，避免被库里已有的评价干扰。
        # 不做隔离的话"样本数"会随历史数据变化，断言会变脆。
        project_id = (
            await db.execute(
                text(
                    "INSERT INTO projects "
                    "(name, description, owner_id, status, "
                    " created_at, updated_at) "
                    "VALUES (:n, NULL, :u, 'active', now(), now()) "
                    "RETURNING id"
                ),
                {
                    "n": f"画像验证 {uuid.uuid4().hex[:6]}",
                    "u": user_id,
                },
            )
        ).scalar_one()
        await db.commit()

    token = create_access_token({"sub": str(user_id)})
    created: list[int] = []

    repeated = "未说明 Redis TTL 的设置依据"

    try:
        async with httpx.AsyncClient(
            base_url=BASE,
            headers={"Authorization": f"Bearer {token}"},
            timeout=120.0,
        ) as client:
            # ================================================
            print()
            print("=" * 74)
            print("1. 样本不足时不下结论")
            print("=" * 74)

            created.append(
                await seed_evaluation(
                    user_id,
                    project_id,
                    {
                        "overall_score": 90,
                        "technical_score": 90,
                        "project_score": 70,
                        "communication_score": 70,
                    },
                    weaknesses=["只有一场"],
                    suggestions=["建议 A"],
                )
            )
            created.append(
                await seed_evaluation(
                    user_id,
                    project_id,
                    {
                        "overall_score": 40,
                        "technical_score": 40,
                        "project_score": 40,
                        "communication_score": 40,
                    },
                    weaknesses=[repeated],
                    suggestions=["建议 B"],
                )
            )

            response = await client.get(
                "/interviews/profile/ability",
                params={"project_id": project_id},
            )
            record(
                "返回 200",
                response.status_code == 200,
                f"status={response.status_code}",
            )

            body = response.json()
            record(
                "识别出 2 场样本",
                body["session_count"] == 2,
                f"count={body['session_count']}",
            )
            record(
                "标记样本不足（不足以判断优势 / 短板）",
                body["sufficient_samples"] is False,
            )
            record(
                "给出了样本不足的说明",
                any(
                    "少于" in item or "只有" in item
                    for item in body["caveats"]
                ),
                f"caveats={len(body['caveats'])} 条",
            )

            # ================================================
            print()
            print("=" * 74)
            print("2. 样本充足时的聚合")
            print("=" * 74)

            created.append(
                await seed_evaluation(
                    user_id,
                    project_id,
                    {
                        "overall_score": 70,
                        "technical_score": 70,
                        "project_score": 70,
                        "communication_score": 70,
                    },
                    weaknesses=[repeated, "另一条问题"],
                    suggestions=["建议 C"],
                    target_role="后端工程师",
                )
            )

            response = await client.get(
                "/interviews/profile/ability",
                params={"project_id": project_id},
            )
            body = response.json()

            record(
                "识别出 3 场样本",
                body["session_count"] == 3,
                f"count={body['session_count']}",
            )
            record(
                "标记样本充足",
                body["sufficient_samples"] is True,
            )

            dimensions = {
                item["key"]: item for item in body["dimensions"]
            }
            record(
                "包含四个维度",
                set(dimensions) == {
                    "overall_score",
                    "technical_score",
                    "project_score",
                    "communication_score",
                },
                f"keys={sorted(dimensions)}",
            )

            overall = dimensions["overall_score"]
            record(
                "中位数正确（[90,40,70] → 70）",
                overall["median"] == 70.0,
                f"median={overall['median']}",
            )
            record(
                "区间正确（min=40 max=90）",
                overall["minimum"] == 40 and overall["maximum"] == 90,
                f"min={overall['minimum']} max={overall['maximum']}",
            )
            record(
                "极差正确（90-40=50）",
                overall["spread"] == 50,
                f"spread={overall['spread']}",
            )
            record(
                "latest 是最后一场",
                overall["latest"] == 70,
                f"latest={overall['latest']}",
            )
            record(
                "history **按时间正序**（旧→新）",
                overall["history"] == [90, 40, 70],
                f"history={overall['history']}",
            )

            # 极差大时必须给出警示 —— 否则用户会把波动当进步
            record(
                "极差大时给出解读警示",
                any("极差" in item for item in body["caveats"]),
                f"caveats={len(body['caveats'])} 条",
            )

            # ================================================
            print()
            print("=" * 74)
            print("3. 重复弱点与逐场明细")
            print("=" * 74)

            record(
                "识别出重复出现的弱点",
                any(
                    item["text"] == repeated
                    and item["occurrences"] == 2
                    for item in body["recurring_weaknesses"]
                ),
                f"recurring={[(w['occurrences'], w['text'][:20]) for w in body['recurring_weaknesses']]}",
            )
            record(
                "只出现一次的弱点不进重复列表",
                all(
                    item["occurrences"] > 1
                    for item in body["recurring_weaknesses"]
                ),
            )
            record(
                "逐场明细含 3 场",
                len(body["sessions"]) == 3,
                f"len={len(body['sessions'])}",
            )
            record(
                "逐场明细带项目名",
                all(
                    item["project_name"]
                    for item in body["sessions"]
                ),
            )
            record(
                "逐场明细带各维度分数",
                all(
                    set(item["scores"])
                    == {
                        "overall_score",
                        "technical_score",
                        "project_score",
                        "communication_score",
                    }
                    for item in body["sessions"]
                ),
            )

            # ================================================
            print()
            print("=" * 74)
            print("4. 隔离与边界")
            print("=" * 74)

            # 别人的画像看不到（路径不接受 user_id）
            response = await client.get(
                "/interviews/profile/ability"
            )
            record(
                "不带 project_id 时返回**自己**的全部评价",
                response.status_code == 200
                and response.json()["session_count"] >= 3,
                f"count={response.json()['session_count']}",
            )

            async with httpx.AsyncClient(
                base_url=BASE, timeout=60.0
            ) as anon:
                response = await anon.get(
                    "/interviews/profile/ability"
                )
            record(
                "未认证返回 401",
                response.status_code == 401,
                f"status={response.status_code}",
            )

            # 一个没有任何评价的项目 → 空画像，不报错
            response = await client.get(
                "/interviews/profile/ability",
                params={"project_id": 999999},
            )
            record(
                "无数据的项目返回空画像（不报错）",
                response.status_code == 200
                and response.json()["session_count"] == 0,
                f"status={response.status_code} "
                f"count={response.json().get('session_count')}",
            )

    finally:
        await cleanup(created)
        async with AsyncSessionLocal() as db:
            await db.execute(
                text("DELETE FROM projects WHERE id = :p"),
                {"p": project_id},
            )
            await db.commit()
        print(f"      已清理会话 {created} 与项目 {project_id}")

    print()
    print("=" * 74)
    passed = sum(1 for _, ok in results if ok)
    failed = [label for label, ok in results if not ok]
    print(f"通过 {passed} / {len(results)}")
    if failed:
        print("失败项:")
        for label in failed:
            print("  -", label)
        return 1

    print("全部通过")
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
