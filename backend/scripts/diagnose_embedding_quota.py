"""诊断嵌入端点的额度状态（只读，不修改任何东西）。

## 为什么要做这个而不只是重试

同一个错误反复出现时，"再试一次"不会产生新信息。
这里做的是**区分几种可能原因**：

| 现象 | 指向 |
| --- | --- |
| 所有模型都报 FreeTierOnly | **账户/工作空间级的"仅用免费额度"开关** |
| 只有 v4 报，其它模型 OK | 该模型单独的免费额度用尽 |
| 报 InvalidApiKey / 无权限 | key 与充值的账号不是同一个 |
| 报 Arrearage / 欠费 | 账号确实欠费 |

测多个模型是关键：它把"账户级"与"模型级"分开。

用法：uv run python -m scripts.diagnose_embedding_quota
"""

import asyncio
import sys

import httpx

from app.core.config import settings

# 候选模型：同一端点、同一个 key，只换模型名。
#
# 包含 v4 之外的型号，用来区分"账户级开关"与"模型级配额"。
CANDIDATE_MODELS = (
    "text-embedding-v4",
    "text-embedding-v3",
    "text-embedding-v2",
    "text-embedding-v1",
)


async def probe(client: httpx.AsyncClient, model: str) -> tuple[str, str]:
    """返回 (结论, 原始信息)。"""

    try:
        response = await client.post(
            f"{settings.embedding_base_url}/embeddings",
            json={"model": model, "input": ["探测"]},
            headers={
                "Authorization": (
                    f"Bearer {settings.embedding_api_key}"
                )
            },
        )
    except Exception as exc:  # noqa: BLE001
        return "网络失败", f"{type(exc).__name__}: {str(exc)[:120]}"

    if response.status_code == 200:
        data = response.json()
        dimension = len(data["data"][0]["embedding"])
        return "可用", f"维度 {dimension}"

    try:
        error = response.json().get("error", {})
    except Exception:  # noqa: BLE001
        error = {}

    code = error.get("code") or ""
    message = (error.get("message") or "")[:130]

    if "FreeTierOnly" in code or "free tier" in message.lower():
        return "额度受限", f"{code}: {message}"

    if "Arrearage" in code or "欠费" in message:
        return "欠费", f"{code}: {message}"

    if (
        "InvalidApiKey" in code
        or response.status_code in (401, 403)
    ):
        return "鉴权失败", f"{code or response.status_code}: {message}"

    return f"其它({response.status_code})", f"{code}: {message}"


async def main() -> int:
    print("=" * 74)
    print("嵌入端点额度诊断")
    print("=" * 74)
    print(f"  端点: {settings.embedding_base_url}")
    print(f"  模型: {settings.embedding_model}")
    print(f"  Key : {settings.embedding_api_key[:6]}"
          f"...{settings.embedding_api_key[-4:]}"
          f"（长度 {len(settings.embedding_api_key)}）")
    print()

    if not settings.embedding_api_key:
        print("  未配置 EMBEDDING_API_KEY")
        return 1

    outcomes: dict[str, str] = {}

    async with httpx.AsyncClient(timeout=30.0) as client:
        for model in CANDIDATE_MODELS:
            conclusion, detail = await probe(client, model)
            outcomes[model] = conclusion
            print(f"  [{conclusion:<10}] {model:<22} {detail}")

    print()
    print("=" * 74)
    print("结论")
    print("=" * 74)

    usable = [
        m for m, c in outcomes.items() if c == "可用"
    ]
    restricted = [
        m for m, c in outcomes.items() if c == "额度受限"
    ]

    if usable:
        print(f"  ✓ 这些模型可用：{usable}")
        if restricted:
            print(
                "  ⚠️ 但它们之外的模型额度受限 —— 说明是**模型级**配额，"
            )
            print("     而不是账户级开关。可以直接改用可用的那个模型。")
            print(
                "     ⚠️ 换模型会让**所有已存向量失效**，"
                "见 MIND_FLOW_PLAN.md §29。"
            )
    elif len(restricted) == len(CANDIDATE_MODELS):
        print("  ✗ **所有**测试的模型都报 FreeTierOnly。")
        print()
        print("     这指向**账户 / 工作空间级的开关**，不是模型配额：")
        print(
            "     阿里云百炼控制台里有一个「仅使用免费额度」选项，"
        )
        print(
            "     开着时**不会动用已充值的余额**。"
        )
        print()
        print("     需要确认三件事：")
        print(
            "       1. 关掉那个「仅使用免费额度」开关"
            "（模型广场 / 费用与配额）"
        )
        print(
            "       2. 充值**到的是这个 Key 所属的账号与工作空间** ——"
        )
        print(
            "          Key 前缀 `sk-ws-` 表示它是 workspace 级的，"
            "充值到别的空间不会生效"
        )
        print(
            "       3. 阿里云账号与百炼（Model Studio）开通的是同一个 ——"
        )
        print(
            "          主账号充值，子账号/工作空间未必共享余额"
        )
    else:
        print("  结果混合，需逐条查看上表。")

    print()
    print("  说明：本脚本**只读**，不修改任何配置。")

    return 0 if usable else 1


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
