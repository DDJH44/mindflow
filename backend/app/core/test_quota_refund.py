"""验证"开始面试失败时退还场次额度"。

## 要防的缺陷（实测复现过）

`start_interview` 的顺序是：

```text
1. consume(INTERVIEW_STARTED)          ← 额度 +1（未提交）
2. apply_transition(..., commit=True)  ← 提交！额度 +1 一起被提交
3. 生成首题（检索 + 嵌入 + LLM）
4. 失败 → db.rollback()                ← 撤销不了第 2 步已提交的东西
```

实测：embedding 额度耗尽导致 start 返回 503，
而 `interview_started` 仍然 +1 —— **用户为一次失败白扣一场**。

## 为什么在进程内打桩，而不是等真实故障

依赖"上游恰好坏着"的测试不是测试，是巧合：
上游恢复后它就走了别的分支，等于没测。
因此这里**用 `ASGITransport` 在进程内发请求**，
并把检索服务替换成必定抛错的实现 —— 失败由测试自己制造。

（同样原因也让它不需要真实前端代理与后端端口。）
"""

import asyncio
import sys

import httpx

from app.core.test_support import grant_quota, reset_quota
from app.database.session import AsyncSessionLocal

results: list[tuple[str, bool]] = []


def record(label: str, ok: bool, extra: str = "") -> None:
    results.append((label, ok))
    suffix = f"  {extra}" if extra else ""
    print(f"  [{'OK  ' if ok else 'FAIL'}] {label}{suffix}")


async def usage_count(user_id: int) -> int:
    from sqlalchemy import text as sql_text

    async with AsyncSessionLocal() as db:
        value = (
            await db.execute(
                sql_text(
                    "SELECT count FROM interview_usage "
                    "WHERE user_id = :u AND metric = 'interview_started'"
                ),
                {"u": user_id},
            )
        ).scalar_one_or_none()

    return int(value or 0)


async def main() -> int:
    sys.path.insert(0, r"D:\yuxi\MindFlow\backend")

    from sqlalchemy import text as sql_text

    from app.core.security import create_access_token
    from app.main import app
    from app.services.retrieval_service import RetrievalService

    async with AsyncSessionLocal() as db:
        user_id = (
            await db.execute(
                sql_text(
                    "SELECT id FROM users WHERE username = :u"
                ),
                {"u": "mindflow"},
            )
        ).scalar_one()

        project_id = (
            await db.execute(
                sql_text(
                    "SELECT id FROM projects WHERE owner_id = :u "
                    "ORDER BY id LIMIT 1"
                ),
                {"u": user_id},
            )
        ).scalar_one()

    await grant_quota(user_id)

    token = create_access_token({"sub": str(user_id)})
    created_sessions: list[int] = []

    original_retrieve = RetrievalService.retrieve

    async def failing_retrieve(*args, **kwargs):
        """模拟上游嵌入/检索不可用。"""

        raise RuntimeError("模拟上游检索失败：额度耗尽")

    transport = httpx.ASGITransport(app=app)

    try:
        async with httpx.AsyncClient(
            transport=transport,
            base_url="http://test",
            headers={"Authorization": f"Bearer {token}"},
            timeout=120.0,
        ) as client:
            # ================================================
            print()
            print("=" * 74)
            print("1. 上游失败时不消耗场次额度")
            print("=" * 74)

            # 打桩：让检索必定失败
            RetrievalService.retrieve = failing_retrieve

            before = await usage_count(user_id)

            created = (
                await client.post(
                    "/api/interviews",
                    json={
                        "project_id": project_id,
                        "target_role": "后端工程师",
                        "max_questions": 2,
                    },
                )
            ).json()
            session_id = created.get("id")
            created_sessions.append(session_id)

            # ⚠️ `ASGITransport` 默认**把未处理的异常重新抛出**给客户端，
            # 而不是返回 500。这正是本用例要测的场景，
            # 因此必须捕获它，否则测试会因为"异常没被处理"而中断 ——
            # 而那恰恰是被测行为本身。
            status_code = 200
            detail = ""

            try:
                response = await client.post(
                    f"/api/interviews/{session_id}/start",
                    json={"query": "考察后端经验"},
                )
                status_code = response.status_code
                detail = str(response.json().get("detail", ""))
            except RuntimeError as exc:
                status_code = 500
                detail = str(exc)

            after = await usage_count(user_id)

            print(f"      start 返回 {status_code}")
            print(f"      场次用量 {before} → {after}")

            record(
                "非 LLM 异常（代码缺陷类）仍原样抛出，不伪装成 503",
                status_code == 500,
                f"status={status_code}",
            )

            # 这是关键断言：**任何**在状态转移已提交之后的失败
            # 都不该消耗场次，而不只是 LLM 类异常。
            #
            # 起初我只在 LLM 分支退还，正是这个用例暴露了缺口。
            record(
                "上游失败时额度**未被消耗**",
                after == before,
                f"{before} → {after}",
            )

            # 会话应留在可继续的状态，而不是丢失
            listing = (
                await client.get("/api/interviews")
            ).json()
            found = [
                item
                for item in listing.get("items", [])
                if item["id"] == session_id
            ]

            record(
                "失败的会话仍可在历史里找到（可继续）",
                bool(found),
                f"状态={found[0]['status'] if found else '找不到'}",
            )

            # ================================================
            print()
            print("=" * 74)
            print("2. LLM 类异常的错误信息不再误导")
            print("=" * 74)

            # 换成 openai 的异常类型，验证映射与文案
            from openai import APITimeoutError

            async def timeout_retrieve(*args, **kwargs):
                raise APITimeoutError(request=None)

            RetrievalService.retrieve = timeout_retrieve

            timed_before = await usage_count(user_id)

            created = (
                await client.post(
                    "/api/interviews",
                    json={
                        "project_id": project_id,
                        "target_role": "后端工程师",
                        "max_questions": 2,
                    },
                )
            ).json()
            created_sessions.append(created.get("id"))

            response = await client.post(
                f"/api/interviews/{created['id']}/start",
                json={"query": "考察后端经验"},
            )

            timed_after = await usage_count(user_id)
            detail = str(response.json().get("detail", ""))

            record(
                "LLM 超时映射为 503",
                response.status_code == 503,
                f"status={response.status_code}",
            )
            record(
                "LLM 超时也退还额度",
                timed_after == timed_before,
                f"{timed_before} → {timed_after}",
            )
            record(
                "错误信息不再声称「没有写入任何数据」",
                "没有写入任何数据" not in detail,
                f"{detail[:50]}",
            )

            # ================================================
            print()
            print("=" * 74)
            print("3. 额度用尽时的回滚（对照路径）")
            print("=" * 74)

            async with AsyncSessionLocal() as db:
                await db.execute(
                    sql_text(
                        "UPDATE interview_usage SET count = 9999 "
                        "WHERE user_id = :u "
                        "AND metric = 'interview_started'"
                    ),
                    {"u": user_id},
                )
                await db.commit()

            exhausted_before = await usage_count(user_id)

            created = (
                await client.post(
                    "/api/interviews",
                    json={
                        "project_id": project_id,
                        "target_role": "后端工程师",
                        "max_questions": 2,
                    },
                )
            ).json()
            created_sessions.append(created.get("id"))

            response = await client.post(
                f"/api/interviews/{created['id']}/start",
                json={"query": "考察后端经验"},
            )

            exhausted_after = await usage_count(user_id)

            record(
                "额度用尽时返回 429",
                response.status_code == 429,
                f"status={response.status_code}",
            )
            record(
                "额度用尽时回滚（计数不继续涨）",
                exhausted_after == 9999,
                f"{exhausted_before} → {exhausted_after}",
            )

    finally:
        RetrievalService.retrieve = original_retrieve

        async with AsyncSessionLocal() as db:
            for session_id in created_sessions:
                if session_id is None:
                    continue
                await db.execute(
                    sql_text(
                        "DELETE FROM interview_questions "
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

        await reset_quota(user_id)
        print(f"      已清理会话 {created_sessions} 并恢复额度")

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
