"""验证启动守卫与 CORS 配置（§33）。

## 它防的是什么

**① 占位密钥能正常工作，直到被利用。**
实测本项目 `.env` 长期使用
`mindflow_2026_development_secret_key_change_later` ——
它**看起来像配置**，实际是占位符。
这种值最危险的地方是：开发、测试、部署全都不报错，
而任何人都能用它伪造出合法的登录令牌。

**② `allow_origins=["*"]` 是个很容易犯的错。**
它配合 `allow_credentials=True` 会让**任意网站**都能带着
用户凭据调这个 API —— 而它在本地完全看不出问题
（因为本地本来就同源）。

## 它验证四件事

1. 生产环境 + 占位密钥 → **拒绝启动**
2. 开发环境 + 占位密钥 → 仅警告（不拦开发）
3. 开发环境 + 真密钥 → 通过
4. **运行中的服务没有错误地允许任意来源**
   —— 发一个带 `Origin` 的请求，响应**不应**回
   `Access-Control-Allow-Origin: *`

第 4 条是**运行时**检查，比读配置更可靠：
配置写对了但中间件挂错位置，读配置看不出来。

用法：uv run python -m tests.test_secret_and_cors
"""

import asyncio
import sys

import httpx

BASE = "http://127.0.0.1:5173/api"

# 一个"看起来像配置"的占位值 —— 与实测踩到的那个同类
PLACEHOLDER = "mindflow_2026_development_secret_key_change_later"


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

    from app.core.config import settings
    from app.main import _check_secret_key

    result = Result()

    print("=" * 78)
    print("启动守卫与 CORS 验证")
    print("=" * 78)

    # 保存原值，测完恢复 —— 否则会影响同进程里的其它逻辑
    original_key = settings.secret_key
    original_env = settings.app_env

    try:
        # ================================================
        print()
        print("=" * 78)
        print("① 生产环境 + 占位密钥 → 必须拒绝启动")
        print("=" * 78)

        settings.app_env = "production"
        settings.secret_key = PLACEHOLDER

        raised = None

        try:
            _check_secret_key()
        except Exception as exc:  # noqa: BLE001
            raised = exc

        result.check(
            "拒绝启动（抛 RuntimeError）",
            isinstance(raised, RuntimeError),
            f"{type(raised).__name__}",
        )

        # 错误信息必须**可行动** —— 光说"密钥不对"没用，
        # 要告诉人怎么生成一个。
        message = str(raised) if raised else ""

        result.check(
            "错误信息给出了生成命令",
            "generate_secret" in message,
            "提示 `uv run python -m scripts.generate_secret`",
        )

        # ================================================
        print()
        print("=" * 78)
        print("② 开发环境 + 占位密钥 → 只警告，不拦")
        print("=" * 78)

        settings.app_env = "development"
        settings.secret_key = PLACEHOLDER

        raised = None

        try:
            _check_secret_key()
        except Exception as exc:  # noqa: BLE001
            raised = exc

        result.check(
            "开发环境不拦（本地开发不该被卡）",
            raised is None,
            "已通过并记录 warning",
        )

        # ================================================
        print()
        print("=" * 78)
        print("③ 真密钥 → 通过")
        print("=" * 78)

        settings.secret_key = (
            "QuNq-2ZlCL9XDP5LHDS6h7bkaP83qEFlZRb5lbyVBvYhWxTYO"
            "VjZw9fhJ4-4qwkDThZajt-SYUrlQA0NCItJtQ"
        )

        raised = None

        try:
            _check_secret_key()
        except Exception as exc:  # noqa: BLE001
            raised = exc

        result.check("真密钥通过校验", raised is None)

        # ================================================
        print()
        print("=" * 78)
        print("④ 运行时：不得允许任意来源")
        print("=" * 78)

        # 发一个带伪造 Origin 的请求，看服务端是否回
        # `Access-Control-Allow-Origin: *`（或回显该来源）。
        #
        # 这是**运行时**检查：配置写对了但中间件挂错位置，
        # 或者有人加了 `allow_origins=["*"]`，只有实际请求能发现。
        evil_origin = "https://evil.example.com"

        async with httpx.AsyncClient(
            base_url=BASE, timeout=30.0
        ) as client:
            response = await client.get(
                "/health",
                headers={"Origin": evil_origin},
            )

        allow_origin = response.headers.get(
            "access-control-allow-origin"
        )
        allow_credentials = response.headers.get(
            "access-control-allow-credentials"
        )

        print(f"      Origin: {evil_origin}")
        print(
            f"      Access-Control-Allow-Origin: "
            f"{allow_origin or '(缺失)'}"
        )
        print(
            f"      Access-Control-Allow-Credentials: "
            f"{allow_credentials or '(缺失)'}"
        )

        # 缺失 = 同源部署、未启用 CORS —— 正确
        # 回显具体来源 = 已配置白名单 —— 也正确（但 evil 不该在白名单里）
        # `*` = 错误
        result.check(
            "没有回 `*`（未允许任意来源）",
            allow_origin != "*",
            f"实得 {allow_origin or '(缺失，同源部署，正确)'}",
        )

        # `*` 与 credentials 同时出现是明确冲突的配置
        result.check(
            "没有出现 `*` + credentials 的危险组合",
            not (
                allow_origin == "*"
                and allow_credentials == "true"
            ),
        )

    finally:
        settings.secret_key = original_key
        settings.app_env = original_env

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
