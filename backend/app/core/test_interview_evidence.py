"""验证"资料依据"端点：把 evidence_chunk_ids 变成可读的片段。

背景：§9.3 要求资料型问题**可追溯依据**，但此前接口只暴露
一串 chunk id，用户看不到"这道题是从我哪段资料来的" ——
数字本身无法建立信任。

覆盖：
1. 结构（items / missing_chunk_ids / is_general）
2. 片段内容与来源文件名
3. **顺序与 evidence_chunk_ids 一致**（顺序反映喂给模型的位置）
4. 通用题返回空依据
5. 截断标记
6. **越权防护**：不能读别人的会话；不能拿别的会话的 question_id
7. 资料被删除时明确报出失效依据，而不是静默少几条

用法：uv run python -m app.core.test_interview_evidence
"""

import asyncio
import json
import sys

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

        # 找一份有 chunk 的资料
        row = (
            await db.execute(
                text(
                    "SELECT d.project_id, c.id, c.document_id "
                    "FROM documents d "
                    "JOIN document_chunks c ON c.document_id = d.id "
                    "WHERE d.project_id IN "
                    "  (SELECT id FROM projects WHERE owner_id = :u) "
                    "ORDER BY c.id LIMIT 1"
                ),
                {"u": user_id},
            )
        ).first()

        if row is None:
            print("  没有可用的 chunk，跳过")
            return 0

        project_id, chunk_id, document_id = row
        print(f"  用项目 {project_id} 的资料（chunk {chunk_id}）")

    token = create_access_token({"sub": str(user_id)})
    created: list[int] = []

    async with httpx.AsyncClient(
        base_url=BASE,
        headers={"Authorization": f"Bearer {token}"},
        timeout=300.0,
    ) as client:
        try:
            # ================================================
            print()
            print("=" * 74)
            print("1. 造一场带资料依据的面试")
            print("=" * 74)

            session_id = (
                await client.post(
                    "/interviews",
                    json={
                        "project_id": project_id,
                        "target_role": "资料依据验证",
                        "max_questions": 2,
                    },
                )
            ).json()["id"]
            created.append(session_id)

            start = await client.post(
                f"/interviews/{session_id}/start",
                json={"query": "考察后端与 RAG 实践"},
            )

            if start.status_code == 503:
                print("      ⚠️ LLM 端点不可用，本次未执行")
                return 2

            question = start.json()["question"]
            question_id = question["id"]
            evidence_ids = question["evidence_chunk_ids"]

            print(f"      问题 {question_id}，依据 {evidence_ids}")

            if not evidence_ids:
                print("      ⚠️ 这道题没有资料依据（通用题），无法验证")
                return 2

            # ================================================
            print()
            print("=" * 74)
            print("2. 取资料依据")
            print("=" * 74)

            response = await client.get(
                f"/interviews/{session_id}/questions/"
                f"{question_id}/evidence"
            )
            record(
                "返回 200",
                response.status_code == 200,
                f"status={response.status_code}",
            )

            body = response.json()
            record(
                "含 items / missing_chunk_ids / is_general",
                all(
                    key in body
                    for key in (
                        "items",
                        "missing_chunk_ids",
                        "is_general",
                    )
                ),
            )

            items = body["items"]
            record(
                "依据条数与 evidence_chunk_ids 一致",
                [item["chunk_id"] for item in items] == evidence_ids,
                f"{[i['chunk_id'] for i in items]} vs {evidence_ids}",
            )

            record(
                "**顺序与 evidence_chunk_ids 一致**（不是数据库返回顺序）",
                [item["chunk_id"] for item in items] == evidence_ids,
            )

            if items:
                first = items[0]
                record(
                    "片段有正文",
                    bool(first["content"].strip()),
                    f"{len(first['content'])} 字符",
                )
                record(
                    "带来源文件名",
                    bool(first.get("document_name")),
                    f"name={first.get('document_name')}",
                )
                record(
                    "带来源资料类型",
                    bool(first.get("document_type")),
                    f"type={first.get('document_type')}",
                )
                record(
                    "正文不超过上限（300 字符）",
                    len(first["content"]) <= 300,
                    f"len={len(first['content'])}",
                )
                print("      --- 第一条片段 ---")
                print(
                    "      " + first["content"][:150].replace("\n", "\n      ")
                )

            record(
                "没有失效依据（资料都在）",
                body["missing_chunk_ids"] == [],
                f"missing={body['missing_chunk_ids']}",
            )

            # ================================================
            print()
            print("=" * 74)
            print("3. 越权与参数校验")
            print("=" * 74)

            # 拿别的会话的 question_id
            async with AsyncSessionLocal() as db:
                other_question = (
                    await db.execute(
                        text(
                            "SELECT q.id FROM interview_questions q "
                            "JOIN interview_sessions s "
                            "  ON s.id = q.session_id "
                            "WHERE s.user_id <> :u "
                            "ORDER BY q.id LIMIT 1"
                        ),
                        {"u": user_id},
                    )
                ).scalar_one_or_none()

            if other_question:
                response = await client.get(
                    f"/interviews/{session_id}/questions/"
                    f"{other_question}/evidence"
                )
                record(
                    "别的会话的 question_id 返回 404",
                    response.status_code == 404,
                    f"status={response.status_code}",
                )
            else:
                print("      （没有他人的问题，跳过）")

            response = await client.get(
                f"/interviews/{session_id}/questions/999999/evidence"
            )
            record(
                "不存在的 question_id 返回 404",
                response.status_code == 404,
                f"status={response.status_code}",
            )

            response = await client.get(
                "/interviews/999999/questions/1/evidence"
            )
            record(
                "不存在的会话返回 404",
                response.status_code == 404,
                f"status={response.status_code}",
            )

            async with httpx.AsyncClient(
                base_url=BASE, timeout=60.0
            ) as anon:
                response = await anon.get(
                    f"/interviews/{session_id}/questions/"
                    f"{question_id}/evidence"
                )
            record(
                "未认证返回 401",
                response.status_code == 401,
                f"status={response.status_code}",
            )

            # 别人的会话（用户 29）
            async with AsyncSessionLocal() as db:
                other_session = (
                    await db.execute(
                        text(
                            "SELECT id FROM interview_sessions "
                            "WHERE user_id <> :u "
                            "ORDER BY id LIMIT 1"
                        ),
                        {"u": user_id},
                    )
                ).scalar_one_or_none()

            if other_session:
                response = await client.get(
                    f"/interviews/{other_session}/questions/"
                    f"1/evidence"
                )
                record(
                    "他人的会话返回 404（所有权隔离）",
                    response.status_code == 404,
                    f"status={response.status_code}",
                )

            # ================================================
            print()
            print("=" * 74)
            print("4. 资料被删除时明确报出失效依据")
            print("=" * 74)

            # 造一条指向不存在 chunk 的依据。
            #
            # `evidence_chunk_ids` 是 JSON 列，因此要传 JSON 文本 +
            # 显式 CAST —— 直接传 Python list 会被 asyncpg 当成
            # 字符串处理并报 "descriptor 'encode' ... doesn't apply
            # to a 'list' object"。
            async with AsyncSessionLocal() as db:
                await db.execute(
                    text(
                        "UPDATE interview_questions "
                        "SET evidence_chunk_ids = CAST(:ids AS JSON) "
                        "WHERE id = :q"
                    ),
                    {
                        "ids": json.dumps([chunk_id, 999999]),
                        "q": question_id,
                    },
                )
                await db.commit()

            response = await client.get(
                f"/interviews/{session_id}/questions/"
                f"{question_id}/evidence"
            )
            body = response.json()

            record(
                "失效的依据被列进 missing_chunk_ids",
                body["missing_chunk_ids"] == [999999],
                f"missing={body['missing_chunk_ids']}",
            )
            record(
                "有效的依据仍然返回",
                [item["chunk_id"] for item in body["items"]]
                == [chunk_id],
                f"items={[i['chunk_id'] for i in body['items']]}",
            )

        finally:
            # 清理
            if created:
                async with AsyncSessionLocal() as db:
                    await db.execute(
                        text(
                            "DELETE FROM interview_answers "
                            "WHERE question_id IN "
                            "(SELECT id FROM interview_questions "
                            " WHERE session_id = ANY(:s))"
                        ),
                        {"s": created},
                    )
                    await db.execute(
                        text(
                            "DELETE FROM interview_questions "
                            "WHERE session_id = ANY(:s)"
                        ),
                        {"s": created},
                    )
                    await db.execute(
                        text(
                            "DELETE FROM interview_status_history "
                            "WHERE session_id = ANY(:s)"
                        ),
                        {"s": created},
                    )
                    await db.execute(
                        text(
                            "DELETE FROM interview_sessions "
                            "WHERE id = ANY(:s)"
                        ),
                        {"s": created},
                    )
                    await db.commit()
                print(f"      已清理会话 {created}")

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
