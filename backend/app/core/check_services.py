"""检查 HTTP 服务是否可达（经 Vite 代理与后端直连）。

## 为什么这必须纳入门禁

`doctor` 检查的是**依赖**（PostgreSQL / Milvus / 迁移 / 模型端点）。
但有一类失败它看不见：**应用与服务本身没在跑**。

实测踩过：后端被停掉后，5 个套件各自报
`httpx.ConnectError: All connection attempts failed` ——
5 条失败信息指向 5 个"不同的套件坏了"，
而真正的原因只有一个：**后端没启动**。

这类噪音会让门禁的结论失去意义：分不清"5 处代码回归"
与"忘了起服务"。因此门禁在跑套件之前必须先确认服务可达。

## 检查项

| 目标 | 用途 |
| --- | --- |
| `http://127.0.0.1:8000/api/health` | 后端直连（`test_async_indexing` 等用）|
| `http://127.0.0.1:5173/api/health` | 经 Vite 代理（`test_logging` / `test_interview_list` 等用）|

两个都查，因为套件用的路径不同 ——
只查一个会让另一半套件在缺失时失败。

用法：uv run python -m app.core.check_services
"""

import sys

import httpx

BACKEND_HEALTH = "http://127.0.0.1:8000/api/health"
PROXY_HEALTH = "http://127.0.0.1:5173/api/health"

TIMEOUT = 5.0


def check(name: str, url: str) -> tuple[bool, str]:
    """探测一个地址，返回 (是否可达, 说明)。

    **不打印** —— 由调用方决定怎么呈现。
    门禁自己会打印，脚本 `main()` 也打印；
    若这里也打印就会出现重复行。
    """

    try:
        response = httpx.get(url, timeout=TIMEOUT)
    except Exception as exc:  # noqa: BLE001
        return False, f"不可达：{type(exc).__name__}"

    if response.status_code != 200:
        return False, f"返回 {response.status_code}（期望 200）"

    return True, "可达"


def probe(name: str, url: str, hint: str) -> bool:
    """探测并打印结果。供脚本与人读使用。"""

    ok, detail = check(name, url)
    icon = "✓" if ok else "✗"

    print(f"   [{icon}] {name:<24} {detail}")

    if not ok:
        print(f"       {hint}")

    return ok


def main() -> int:
    print("=" * 74)
    print("HTTP 服务可达性")
    print("=" * 74)

    # **两个都探测**，不用 `and` 短路 ——
    # 短路会让"两个都不可达"只报出第一个，
    # 于是修完一个再跑又发现第二个，白跑一轮。
    backend_ok = probe(
        "后端 :8000",
        BACKEND_HEALTH,
        "启动：uv run uvicorn app.main:app --host 127.0.0.1 "
        "--port 8000 --no-access-log",
    )

    proxy_ok = probe(
        "前端代理 :5173",
        PROXY_HEALTH,
        "启动：cd web && npx vite --port 5173",
    )

    print()

    if backend_ok and proxy_ok:
        print("   ✓ 服务均可达")
        return 0

    print(
        "   ✗ 有服务不可达。**在解决它之前，套件的失败不能"
        "作为代码问题的证据** —— 多个套件会各报一次连接失败，"
        "而真因只有一个。"
    )
    return 1


if __name__ == "__main__":
    sys.exit(main())
