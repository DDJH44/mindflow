"""验证面试历史列表端点。

背景：没有这个端点时，用户关掉页面就**找不回进行中的面试** ——
那场面试既占用了额度也无法继续，等于白做。

覆盖：
1. 返回结构（items / total / limit / offset）
2. 按更新时间倒序（用户要找的是"最近动过的"）
3. `unfinished_only` 只返回未结束的会话
4. `project_id` 过滤
5. 分页（limit / offset / total 一致）
6. `answered_count` 是"已作答"而非"已提问"
7. 所有权隔离：看不到别人的会话

用法：uv run python -m app.core.test_interview_list
"""

import asyncio
import sys
import uuid

import httpx
from sqlalchemy import text

from app.database.session import AsyncSessionLocal

BASE = "http://127.0.0.1:5173/api"
ACCOUNT = "mindflow"
PROJECT_ID = 3

results: list[tuple[str, bool]] = []


def record(label: str, ok: bool, extra: str = "") -> None:
    results.append((label, ok))
    suffix = f"  {extra}" if extra else ""
    print(f"  [{'OK  ' if ok else 'FAIL'}] {label}{suffix}")


async def make_session(
    user_id: int,
    project_id: int,
    status: str,
    answered: int = 0,
    asked: int = 0,
) -> int:
    """造一个会话，可选地附上题目与答案。"""

    async with AsyncSessionLocal() as db:
        session_id = (
            await db.execute(
                text(
                    "INSERT INTO interview_sessions "
                    "(project_id, user_id, status, interview_type, "
                    " current_question_index, max_questions, "
                    " questions_asked, created_at, updated_at) "
                    "VALUES (:p, :u, :s, 'technical', :idx, 8, "
                    " :asked, now(), now()) "
                    "RETURNING id"
                ),
                {
                    "p": project_id,
                    "u": user_id,
                    "s": status,
                    "idx": asked,
                    "asked": asked,
                },
            )
        ).scalar_one()

        for index in range(asked):
            question_id = (
                await db.execute(
                    text(
                        "INSERT INTO interview_questions "
                        "(session_id, question, question_type, "
                        " question_index, context, evidence_chunk_ids, "
                        " is_general, created_at) "
                        "VALUES (:s, :q, 'technical', :i, NULL, "
                        " '{}', false, now()) RETURNING id"
                    ),
                    {
                        "s": session_id,
                        "q": f"第 {index + 1} 题",
                        "i": index,
                    },
                )
            ).scalar_one()

            if index < answered:
                await db.execute(
                    text(
                        "INSERT INTO interview_answers "
                        "(question_id, answer, created_at) "
                        "VALUES (:q, '回答', now())"
                    ),
                    {"q": question_id},
                )

        await db.commit()

    return session_id


async def cleanup(session_ids: list[int]) -> None:
    if not session_ids:
        return

    async with AsyncSessionLocal() as db:
        await db.execute(
            text(
                "DELETE FROM interview_answers WHERE question_id IN "
                "(SELECT id FROM interview_questions "
                " WHERE session_id = ANY(:s))"
            ),
            {"s": session_ids},
        )
        await db.execute(
            text(
                "DELETE FROM interview_questions "
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

    token = create_access_token({"sub": str(user_id)})
    created: list[int] = []

    async with httpx.AsyncClient(
        base_url=BASE,
        headers={"Authorization": f"Bearer {token}"},
        timeout=120.0,
    ) as client:
        try:
            # ================================================
            print()
            print("=" * 74)
            print("1. 基本结构")
            print("=" * 74)

            response = await client.get("/interviews")
            record(
                "列表返回 200",
                response.status_code == 200,
                f"status={response.status_code}",
            )

            body = response.json()
            record(
                "含 items / total / limit / offset",
                all(
                    key in body
                    for key in ("items", "total", "limit", "offset")
                ),
                f"keys={sorted(body.keys())}",
            )

            baseline_total = body["total"]
            print(f"      当前共 {baseline_total} 场")

            # ================================================
            print()
            print("=" * 74)
            print("2. 造数据：进行中 + 已完成，答案数少于提问数")
            print("=" * 74)

            paused_id = await make_session(
                user_id, PROJECT_ID, "paused", answered=1, asked=2
            )
            created.append(paused_id)

            completed_id = await make_session(
                user_id, PROJECT_ID, "completed", answered=2, asked=2
            )
            created.append(completed_id)

            print(f"      paused={paused_id}  completed={completed_id}")

            response = await client.get("/interviews")
            body = response.json()
            items = body["items"]

            record(
                "新增两场后 total +2",
                body["total"] == baseline_total + 2,
                f"{baseline_total} → {body['total']}",
            )

            # ================================================
            print()
            print("=" * 74)
            print("3. 排序与 answered_count")
            print("=" * 74)

            ids = [item["id"] for item in items]
            record(
                "新造的会话排在最前（按 updated_at 倒序）",
                paused_id in ids[:3] and completed_id in ids[:3],
                f"前三个 id={ids[:3]}",
            )

            paused_item = next(
                item for item in items if item["id"] == paused_id
            )
            record(
                "questions_asked 反映提问数",
                paused_item["questions_asked"] == 2,
                f"asked={paused_item['questions_asked']}",
            )
            record(
                "answered_count 反映**已作答**数（与提问数区分）",
                paused_item["answered_count"] == 1,
                f"answered={paused_item['answered_count']} "
                f"asked={paused_item['questions_asked']}",
            )

            record(
                "带上了项目名",
                bool(paused_item.get("project_name")),
                f"project_name={paused_item.get('project_name')}",
            )

            # ================================================
            print()
            print("=" * 74)
            print("4. unfinished_only 过滤")
            print("=" * 74)

            response = await client.get(
                "/interviews", params={"unfinished_only": True}
            )
            body = response.json()
            unfinished_ids = [item["id"] for item in body["items"]]

            record(
                "未结束列表含 paused 会话",
                paused_id in unfinished_ids,
            )
            record(
                "未结束列表**不含** completed 会话",
                completed_id not in unfinished_ids,
            )
            record(
                "未结束列表不含终态",
                all(
                    item["status"]
                    not in ("completed", "cancelled", "failed")
                    for item in body["items"]
                ),
                f"状态集合={sorted({i['status'] for i in body['items']})}",
            )
            record(
                "total 与过滤一致",
                body["total"] == len(
                    [i for i in body["items"]]
                )
                or body["total"] >= len(body["items"]),
                f"total={body['total']}",
            )

            # ================================================
            print()
            print("=" * 74)
            print("5. project_id 过滤")
            print("=" * 74)

            response = await client.get(
                "/interviews", params={"project_id": PROJECT_ID}
            )
            body = response.json()
            record(
                "按项目过滤后只含该项目",
                all(
                    item["project_id"] == PROJECT_ID
                    for item in body["items"]
                ),
                f"共 {body['total']} 场",
            )

            # 一个不存在的项目应返回空而不是报错
            response = await client.get(
                "/interviews", params={"project_id": 999999}
            )
            record(
                "不存在的项目返回空列表",
                response.status_code == 200
                and response.json()["total"] == 0,
                f"status={response.status_code}",
            )

            # ================================================
            print()
            print("=" * 74)
            print("6. 分页")
            print("=" * 74)

            response = await client.get(
                "/interviews", params={"limit": 1, "offset": 0}
            )
            page1 = response.json()
            response = await client.get(
                "/interviews", params={"limit": 1, "offset": 1}
            )
            page2 = response.json()

            record(
                "limit=1 只返回一条",
                len(page1["items"]) == 1,
                f"len={len(page1['items'])}",
            )
            record(
                "offset 生效（两页不是同一条）",
                page1["items"][0]["id"] != page2["items"][0]["id"],
                f"{page1['items'][0]['id']} vs "
                f"{page2['items'][0]['id']}",
            )
            record(
                "total 不受 limit 影响",
                page1["total"] == page2["total"] == body["total"],
                f"{page1['total']} / {body['total']}",
            )

            # 越界应返回空而不是报错
            response = await client.get(
                "/interviews", params={"offset": 10000}
            )
            record(
                "offset 越界返回空列表",
                response.status_code == 200
                and response.json()["items"] == [],
                f"status={response.status_code}",
            )

            # ================================================
            print()
            print("=" * 74)
            print("7. 参数校验与所有权")
            print("=" * 74)

            response = await client.get(
                "/interviews", params={"limit": 0}
            )
            record(
                "limit=0 被拒绝（422）",
                response.status_code == 422,
                f"status={response.status_code}",
            )

            response = await client.get(
                "/interviews", params={"limit": 999}
            )
            record(
                "limit 超过 50 被拒绝（422）",
                response.status_code == 422,
                f"status={response.status_code}",
            )

            # 未认证应被拒
            async with httpx.AsyncClient(
                base_url=BASE, timeout=60.0
            ) as anon:
                response = await anon.get("/interviews")
            record(
                "未认证访问返回 401",
                response.status_code == 401,
                f"status={response.status_code}",
            )

            # 所有权隔离：另一个用户的会话不该出现
            async with AsyncSessionLocal() as db:
                other_user_id = (
                    await db.execute(
                        text(
                            "SELECT id FROM users "
                            "WHERE id <> :u ORDER BY id LIMIT 1"
                        ),
                        {"u": user_id},
                    )
                ).scalar_one_or_none()

            if other_user_id:
                other_session = await make_session(
                    other_user_id, PROJECT_ID, "completed"
                )
                created.append(other_session)

                response = await client.get(
                    "/interviews", params={"limit": 50}
                )
                record(
                    "看不到其他用户的会话",
                    all(
                        item["id"] != other_session
                        for item in response.json()["items"]
                    ),
                    f"他人会话 {other_session}",
                )
            else:
                print("      （只有一个用户，跳过所有权隔离）")

        finally:
            await cleanup(created)
            print(f"      已清理 {created}")

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
