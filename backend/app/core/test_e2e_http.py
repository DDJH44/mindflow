"""端到端验证：完全通过 HTTP（经 Vite 代理）走完前端使用的链路。

与其它脚本的区别：
- 其它脚本用 `ASGITransport` 直连应用、绕过网络与代理
- 本脚本打 `http://127.0.0.1:5173/api/...`，即**前端真正请求的地址**，
  因此同时验证了后端进程、Vite 代理与前端所依赖的全部端点

需要：Docker（PostgreSQL + Milvus）、后端 8000、Vite 5173、LLM。

用法：uv run python -m app.core.test_e2e_http
"""

import asyncio
import sys
import uuid

import httpx

BASE = "http://127.0.0.1:5173/api"

SAMPLE_RESUME = (
    "姓名：测试候选人\n\n"
    "项目经历：MindFlow AI 面试助手（后端负责人）\n"
    "使用 FastAPI 构建接口层，PostgreSQL 存结构化数据，"
    "Milvus 存向量，Redis 做缓存。\n\n"
    "文档处理：简历解析为纯文本后按 500 字符切块、overlap 100，"
    "用 text-embedding-v4 转成 1024 维向量写入 Milvus。\n\n"
    "检索：查询向量化后带 project_id 过滤取 top-5，"
    "按 chunk_id 回 PostgreSQL 取正文，再交给大模型生成面试问题。\n\n"
    "踩过的坑：固定长度切块会把一段项目经历截断，"
    "导致检索只命中半个上下文，后来加了重叠窗口缓解。\n"
) * 2

results: list[tuple[str, bool]] = []


def record(label: str, ok: bool, extra: str = "") -> None:
    results.append((label, ok))
    suffix = f"  {extra}" if extra else ""
    print(f"  [{'OK  ' if ok else 'FAIL'}] {label}{suffix}")


async def delete_session(session_id: int) -> bool:
    """删除走查/e2e 造出的会话及其关联数据。

    顺序很重要：先删**依赖行**（答案 → 题目 → 评价 → 轨迹），
    再删会话本身。只删会话会因外键报错，而外层若吞掉异常，
    数据就留下了 —— 那种"清理失败但没人发现"最难查。
    """

    try:
        from sqlalchemy import text as sql_text

        from app.database.session import AsyncSessionLocal

        async with AsyncSessionLocal() as db:
            await db.execute(
                sql_text(
                    "DELETE FROM interview_answers "
                    "WHERE question_id IN "
                    "(SELECT id FROM interview_questions "
                    " WHERE session_id = :s)"
                ),
                {"s": session_id},
            )
            await db.execute(
                sql_text(
                    "DELETE FROM interview_questions "
                    "WHERE session_id = :s"
                ),
                {"s": session_id},
            )
            await db.execute(
                sql_text(
                    "DELETE FROM interview_evaluations "
                    "WHERE session_id = :s"
                ),
                {"s": session_id},
            )
            await db.execute(
                sql_text(
                    "DELETE FROM interview_status_history "
                    "WHERE session_id = :s"
                ),
                {"s": session_id},
            )
            await db.execute(
                sql_text(
                    "DELETE FROM interview_sessions WHERE id = :s"
                ),
                {"s": session_id},
            )
            await db.commit()

    except Exception as exc:  # noqa: BLE001
        print(f"      清理会话失败: {type(exc).__name__}: {exc}")
        return False

    return True


async def delete_test_user(username: str) -> bool:
    """删除本次注册的测试用户及其遗留数据与向量。

    顺序：先删项目（走记录），再删用户，最后清孤儿向量 ——
    级联删除不会碰 Milvus。
    """

    try:
        from sqlalchemy import text as sql_text

        from app.database.session import AsyncSessionLocal

        async with AsyncSessionLocal() as db:
            user_id = (
                await db.execute(
                    sql_text(
                        "SELECT id FROM users WHERE username = :u"
                    ),
                    {"u": username},
                )
            ).scalar_one_or_none()

            if user_id is None:
                return True

            await db.execute(
                sql_text(
                    "DELETE FROM interview_usage WHERE user_id = :u"
                ),
                {"u": user_id},
            )
            await db.execute(
                sql_text(
                    "DELETE FROM projects WHERE owner_id = :u"
                ),
                {"u": user_id},
            )
            await db.execute(
                sql_text("DELETE FROM users WHERE id = :u"),
                {"u": user_id},
            )
            await db.commit()

    except Exception as exc:  # noqa: BLE001
        print(f"      清理测试用户失败: {type(exc).__name__}: {exc}")
        return False

    # 向量收尾：级联删除留下的向量会变成孤儿
    try:
        from app.core.cleanup_test_users import purge_orphan_vectors

        purged = await purge_orphan_vectors()
        if purged:
            print(f"      顺带清理孤儿向量 {purged} 个")
    except Exception:  # noqa: BLE001
        pass

    return True


async def main():
    suffix = uuid.uuid4().hex[:8]
    username = f"e2e_{suffix}"
    password = "e2e_pass_123"

    created_project_id = None
    created_document_id = None
    created_session_id = None

    async with httpx.AsyncClient(
        base_url=BASE,
        timeout=600.0,
    ) as client:

        # ================================================
        # 1. 注册 + 登录（前端登录页走的路径）
        # ================================================
        print()
        print("=" * 74)
        print("1. 注册与登录")
        print("=" * 74)

        response = await client.post(
            "/auth/register",
            json={
                "username": username,
                "email": f"{username}@example.com",
                "password": password,
            },
        )
        record(
            "注册返回 201",
            response.status_code == 201,
            f"status={response.status_code}",
        )

        response = await client.post(
            "/auth/login",
            json={"account": username, "password": password},
        )
        record(
            "登录返回 200",
            response.status_code == 200,
            f"status={response.status_code}",
        )

        token = response.json().get("access_token")
        record("拿到 access_token", bool(token))

        if not token:
            print("      无法继续：没有 token")
            return

        client.headers["Authorization"] = f"Bearer {token}"

        response = await client.get("/auth/me")
        record(
            "/auth/me 可用（登录态恢复）",
            response.status_code == 200
            and response.json().get("username") == username,
        )

        # 新用户默认额度
        response = await client.get("/usage")
        record(
            "额度端点返回两份额度",
            response.status_code == 200
            and "interviews" in response.json()
            and "questions" in response.json(),
            f"interviews={response.json().get('interviews', {}).get('quota')} "
            f"questions={response.json().get('questions', {}).get('quota')}",
        )

        # ================================================
        # 2. 建项目 + 传资料（资料页走的路径）
        # ================================================
        print()
        print("=" * 74)
        print("2. 建项目与上传资料")
        print("=" * 74)

        response = await client.post(
            "/projects",
            json={"name": f"E2E 项目 {suffix}"},
        )
        record(
            "建项目返回 201",
            response.status_code == 201,
            f"status={response.status_code}",
        )
        created_project_id = response.json().get("id")

        response = await client.post(
            f"/projects/{created_project_id}/documents",
            params={"document_type": "resume"},
            files={
                "file": (
                    "e2e_resume.txt",
                    SAMPLE_RESUME.encode("utf-8"),
                    "text/plain",
                )
            },
        )
        record(
            "上传资料返回 201",
            response.status_code == 201,
            f"status={response.status_code}",
        )

        if response.status_code != 201:
            print("      响应体:", response.text[:300])

        document = (
            response.json() if response.status_code == 201 else {}
        )
        created_document_id = document.get("id")

        record(
            "资料已建立向量索引（status=embedded）",
            document.get("status") == "embedded",
            f"status={document.get('status')}",
        )

        # ================================================
        # 3. 开始面试（首页走的路径）
        # ================================================
        print()
        print("=" * 74)
        print("3. 创建会话并开始面试（生成首题）")
        print("=" * 74)

        response = await client.post(
            "/interviews",
            json={
                "project_id": created_project_id,
                "target_role": "后端工程师",
                "max_questions": 4,
            },
        )
        record(
            "建会话返回 201",
            response.status_code == 201,
            f"status={response.status_code}",
        )
        created_session_id = response.json().get("id")

        response = await client.post(
            f"/interviews/{created_session_id}/start",
            json={"query": "考察后端与 RAG 实践经验"},
        )
        record(
            "开始面试返回 200",
            response.status_code == 200,
            f"status={response.status_code}",
        )

        if response.status_code != 200:
            print("      响应体:", response.text[:300])

        start_body = (
            response.json() if response.status_code == 200 else {}
        )
        question = start_body.get("question") or {}

        record(
            "生成了首题",
            bool(question.get("question")),
        )

        # 这是 D51 的业务效果：题目应带**资料依据**
        record(
            "首题带资料依据（证明检索生效）",
            bool(question.get("evidence_chunk_ids")),
            f"evidence={question.get('evidence_chunk_ids')}",
        )

        if question.get("question"):
            print(f"      首题: {question['question'][:70]}…")

        # ================================================
        # 4. 逐题作答（面试页走的路径）
        # ================================================
        print()
        print("=" * 74)
        print("4. 逐题作答（含自动追问）")
        print("=" * 74)

        answer_text = (
            "我在 MindFlow 项目负责后端。简历解析后按 500 字符切块、"
            "overlap 100，用 text-embedding-v4 生成 1024 维向量存入 Milvus。"
            "检索时带 project_id 过滤取 top-5，按 chunk_id 回 PostgreSQL "
            "取正文再交给大模型。选 500 是因为一段项目经历通常 400~600 字，"
            "再大会把多个项目混进一个块、增加噪声。"
        )

        current_question_id = question.get("id")
        rounds = 0

        while current_question_id and rounds < 6:
            rounds += 1

            response = await client.post(
                f"/interviews/{created_session_id}/answer",
                json={
                    "question_id": current_question_id,
                    "answer": answer_text,
                },
            )

            record(
                f"第 {rounds} 轮作答返回 200",
                response.status_code == 200,
                f"status={response.status_code}",
            )

            if response.status_code != 200:
                print("      响应体:", response.text[:300])
                break

            body = response.json()

            # 预算耗尽时后端会自动结束并带回评价
            if body.get("finished"):
                record(
                    "达到题目预算后自动结束并返回评价",
                    body.get("evaluation") is not None,
                    f"reason={body.get('termination_reason')}",
                )
                break

            follow_up = body.get("follow_up_question")
            if not follow_up:
                break

            current_question_id = follow_up["id"]

        # ================================================
        # 5. 详情（面试页 / 报告页走的路径）
        # ================================================
        print()
        print("=" * 74)
        print("5. 读取详情与报告")
        print("=" * 74)

        response = await client.get(
            f"/interviews/{created_session_id}/detail"
        )
        record(
            "详情返回 200",
            response.status_code == 200,
            f"status={response.status_code}",
        )

        detail = response.json()
        questions = detail.get("questions", [])
        record(
            "详情含多轮问答",
            len(questions) >= 2,
            f"共 {len(questions)} 题",
        )
        record(
            "题目的资料依据已持久化",
            any(item.get("evidence_chunk_ids") for item in questions),
        )

        # ================================================
        # 6. 结束并生成报告
        # ================================================
        print()
        print("=" * 74)
        print("6. 结束面试并生成报告")
        print("=" * 74)

        response = await client.post(
            f"/interviews/{created_session_id}/finish"
        )

        if response.status_code == 409:
            # 已因预算耗尽自动结束，这也算通过
            record(
                "结束面试（已自动结束，返回 409 属预期）",
                True,
                f"status={response.status_code}",
            )
        else:
            record(
                "结束面试返回 200",
                response.status_code == 200,
                f"status={response.status_code}",
            )
            if response.status_code != 200:
                print("      结束响应体:", response.text[:300])

        response = await client.get(
            f"/interviews/{created_session_id}/detail"
        )
        detail = response.json()
        evaluation = detail.get("evaluation")

        record(
            "报告含整场评价",
            evaluation is not None,
        )

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
            # 断言"字段结构可用且至少有内容"，而不是"三组都非空"。
            #
            # 为什么：实测模型偶尔某一组返回空（例如 strengths=0）——
            # 这是**模型输出波动**（ADR-037 已定不再调提示词），
            # 不是解析丢内容：解析若丢字段，该字段会**每次**都空。
            # 而"三组都非空"也不是产品要求 —— 一份全是优点的回答
            # 本来就可能没有"不足"可写。
            structured = {
                "strengths": evaluation.get("strengths"),
                "weaknesses": evaluation.get("weaknesses"),
                "suggestions": evaluation.get("suggestions"),
            }
            total = sum(
                len(value or []) for value in structured.values()
            )

            record(
                "评价含结构化内容（前端报告页要渲染）",
                all(
                    isinstance(value, list)
                    for value in structured.values()
                )
                and total > 0,
                f"共 {total} 条 "
                f"s={len(structured['strengths'] or [])} "
                f"w={len(structured['weaknesses'] or [])} "
                f"g={len(structured['suggestions'] or [])}",
            )

        # ================================================
        # 7. 清理（走 DELETE 端点，顺带验证它）
        # ================================================
        print()
        print("=" * 74)
        print("7. 清理")
        print("=" * 74)

        # 会话先删。
        #
        # 不删的话它会留在库里：后面删测试用户虽然会把会话一起
        # 级联掉，但**那把它的向量留成了孤儿**（D52），
        # 而孤儿向量会占用 top-k 名额、静默降低召回。
        if created_session_id:
            deleted = await delete_session(created_session_id)
            record(
                "删除测试会话",
                deleted,
                f"session={created_session_id}",
            )

        if created_document_id:
            response = await client.delete(
                f"/projects/{created_project_id}/documents/"
                f"{created_document_id}"
            )
            record(
                "删除测试资料返回 204",
                response.status_code == 204,
                f"status={response.status_code}",
            )

        if created_project_id:
            response = await client.delete(
                f"/projects/{created_project_id}"
            )
            record(
                "删除测试项目返回 204",
                response.status_code == 204,
                f"status={response.status_code}",
            )

    # 注册出来的测试用户也要清掉。
    #
    # 不清理的话每跑一次就多一个用户，开发库很快被 e2e_xxx 淹没
    # （实测确实如此）。**注意先删项目再删用户**：级联删除
    # 不会清 Milvus 里的向量，最后统一由 check_orphan_vectors 收尾。
    cleaned_user = await delete_test_user(username)
    print(f"  已清理测试用户 {username}: {cleaned_user}")

    # 汇总
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
