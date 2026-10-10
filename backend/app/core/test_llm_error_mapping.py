"""验证上游 LLM 失败被映射成 503（D57），而不是裸 500。

为什么需要：LLM SDK 的超时/连接异常继承自 `openai.OpenAIError`，
**既不是 `ValueError` 也不是 `RuntimeError`**。生成题目、分析回答、
整场评价三处路由只处理了后两者，于是上游一慢就变成
`{"detail": "Internal Server Error"}` —— 客户端拿不到任何提示，
运维也分不清是上游问题还是代码 bug。

本套件需要**两个后端**：

1. 正常后端（8000）：验证业务异常仍走原来的状态码
2. 故意指向不可达 LLM 的后端（8099）：
   验证 LLM 失败返回 503

启动第二个后端：

    $env:LLM_BASE_URL="http://127.0.0.1:9/v1"
    $env:LLM_API_KEY="sk-broken-on-purpose"
    uv run uvicorn app.main:app --host 127.0.0.1 --port 8099

用法：uv run python -m app.core.test_llm_error_mapping
"""

import asyncio
import sys

import httpx
from sqlalchemy import text

from app.core.security import create_access_token
from app.database.session import AsyncSessionLocal

BROKEN_BASE = "http://127.0.0.1:8099/api"
ACCOUNT = "mindflow"

results: list[tuple[str, bool]] = []


def record(label: str, ok: bool, extra: str = "") -> None:
    results.append((label, ok))
    suffix = f"  {extra}" if extra else ""
    print(f"  [{'OK  ' if ok else 'FAIL'}] {label}{suffix}")


async def cleanup_session(session_id: int | None) -> None:
    if not session_id:
        return

    async with AsyncSessionLocal() as db:
        await db.execute(
            text(
                "DELETE FROM interview_status_history "
                "WHERE session_id = :s"
            ),
            {"s": session_id},
        )
        await db.execute(
            text(
                "DELETE FROM interview_answers WHERE question_id IN "
                "(SELECT id FROM interview_questions "
                " WHERE session_id = :s)"
            ),
            {"s": session_id},
        )
        await db.execute(
            text(
                "DELETE FROM interview_questions WHERE session_id = :s"
            ),
            {"s": session_id},
        )
        await db.execute(
            text("DELETE FROM interview_sessions WHERE id = :s"),
            {"s": session_id},
        )
        await db.commit()


async def main() -> int:
    sys.path.insert(0, r"D:\yuxi\MindFlow\backend")

    async with AsyncSessionLocal() as db:
        user_id = (
            await db.execute(
                text("SELECT id FROM users WHERE username = :u"),
                {"u": ACCOUNT},
            )
        ).scalar_one()
        project_id = (
            await db.execute(
                text(
                    "SELECT id FROM projects "
                    "WHERE owner_id = :u ORDER BY id LIMIT 1"
                ),
                {"u": user_id},
            )
        ).scalar_one()

    token = create_access_token({"sub": str(user_id)})
    session_id = None

    async with httpx.AsyncClient(
        base_url=BROKEN_BASE,
        headers={"Authorization": f"Bearer {token}"},
        timeout=300.0,
    ) as client:
        # ================================================
        print()
        print("=" * 74)
        print("1. LLM 不可达时 /start 的响应")
        print("=" * 74)

        response = await client.post(
            "/interviews",
            json={
                "project_id": project_id,
                "target_role": "LLM 映射验证",
                "max_questions": 2,
            },
        )

        if response.status_code != 201:
            print(
                f"      建会话失败 {response.status_code}；"
                "请确认 8099 的后端已启动"
            )
            return 1

        session_id = response.json()["id"]

        response = await client.post(
            f"/interviews/{session_id}/start",
            json={"query": "验证 LLM 失败映射"},
        )

        detail = ""
        try:
            detail = str(response.json().get("detail", ""))
        except Exception:  # noqa: BLE001
            detail = response.text[:200]

        print(f"      status={response.status_code}")
        print(f"      detail={detail[:150]}")

        record(
            "返回 503（而不是裸 500）",
            response.status_code == 503,
            f"status={response.status_code}",
        )
        record("detail 说明是模型调用失败", "调用模型失败" in detail)
        record("detail 含异常类名（便于排查）", "Error" in detail)
        record(
            "detail 说明没有写入数据",
            "没有写入任何数据" in detail,
        )

        # ================================================
        print()
        print("=" * 74)
        print("2. 业务异常不被误判成 LLM 问题")
        print("=" * 74)

        # 用**不经过 LLM** 的非法转移来验证 409 仍是 409。
        # 不用"重复 start"：那条路径会重新调模型，返回 503 是正确的。
        response = await client.post(
            f"/interviews/{session_id}/transition",
            json={"target_status": "completed"},
        )
        record(
            "非法转移仍是 409",
            response.status_code == 409,
            f"status={response.status_code}",
        )

        response = await client.post(
            "/interviews/999999/start",
            json={"query": "x"},
        )
        record(
            "不存在的会话仍是 404",
            response.status_code == 404,
            f"status={response.status_code}",
        )

        async with httpx.AsyncClient(
            base_url=BROKEN_BASE, timeout=60.0
        ) as anon:
            response = await anon.get("/interviews")
        record(
            "未认证仍是 401",
            response.status_code == 401,
            f"status={response.status_code}",
        )

        # 额度不足仍是 429（不经过 LLM 的只读端点）
        response = await client.get("/usage")
        record(
            "额度端点仍正常（200）",
            response.status_code == 200,
            f"status={response.status_code}",
        )

    await cleanup_session(session_id)
    if session_id:
        print(f"      已清理会话 {session_id}")

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
