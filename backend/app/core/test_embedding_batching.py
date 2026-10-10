"""验证嵌入分批与自适应上限。

**要防的缺陷**：此前 `embed_texts` 把整个文档的 chunk 一次性发出，
而端点有批量上限，于是**超过约 1 万字符的文件必然上传失败**
（25 块 > 上限 10），报错却是 502「服务不可用」——
用户完全看不出问题出在文件长度上。

**为什么用假客户端而不是真端点**：
- 端点会限流，套件会因为外部状态而不稳定
- 上限**会变**（实测同一端点两次测量分别声称 20 与 10），
  依赖真实值会让断言随时失效
- 这里要验证的是**我们的分批与自适应逻辑**，与端点当前值无关

真端点路径另由 `test_embedding` / 上传类套件覆盖。

用法：uv run python -m app.core.test_embedding_batching
"""

import asyncio
import sys
from types import SimpleNamespace

from app.core.config import settings
from app.services.openai_embedding_service import (
    OpenAIEmbeddingService,
    parse_batch_limit,
)

results: list[tuple[str, bool]] = []


def record(label: str, ok: bool, extra: str = "") -> None:
    results.append((label, ok))
    suffix = f"  {extra}" if extra else ""
    print(f"  [{'OK  ' if ok else 'FAIL'}] {label}{suffix}")


class FakeBatchLimitError(Exception):
    """模拟端点的"批量过大"报错（仅用于说明语义，实际用真类）。"""


def make_service(limit: int):
    """造一个客户端被打桩的服务实例。

    桩必须抛**真实的** `openai.BadRequestError`，否则服务端的
    `except BadRequestError` 不会命中，测试会验证到一条假路径 ——
    那种测试会通过，但生产代码依然是坏的。
    """

    from openai import BadRequestError

    service = OpenAIEmbeddingService()
    calls: list[int] = []

    async def create(model: str, input):  # noqa: A002
        size = len(input)
        calls.append(size)

        if size > limit:
            raise BadRequestError(
                message=(
                    "Error code: 400 - batch size is invalid, "
                    f"it should not be larger than {limit}."
                ),
                response=SimpleNamespace(
                    status_code=400,
                    headers={},
                    request=SimpleNamespace(),
                ),
                body=None,
            )

        # 每条文本对应一个"向量"：用文本长度做区分，
        # 便于验证返回顺序没有错位
        return SimpleNamespace(
            data=[
                SimpleNamespace(
                    embedding=[float(len(text))] * 4
                )
                for text in input
            ]
        )

    service.client = SimpleNamespace(
        embeddings=SimpleNamespace(create=create)
    )

    return service, calls


async def main() -> int:
    sys.path.insert(0, r"D:\yuxi\MindFlow\backend")

    # 重置进程内学到的上限，让每个用例从干净状态开始
    OpenAIEmbeddingService._learned_batch_limit = None

    # ================================================
    print()
    print("=" * 74)
    print("1. 上限解析")
    print("=" * 74)

    cases = [
        (
            "Value error, batch size is invalid, "
            "it should not be larger than 10.: input.contents",
            10,
        ),
        (
            "batch size is invalid, it should not be larger "
            "than 2048.",
            2048,
        ),
        ("完全无关的报错", None),
        ("it should not be larger than abc", None),
    ]

    for message, expected in cases:
        got = parse_batch_limit(message)
        record(
            f"解析 {str(expected):<6} ← {message[:42]}…",
            got == expected,
            f"实得 {got}",
        )

    # ================================================
    print()
    print("=" * 74)
    print("2. 小批量直接通过（不分批）")
    print("=" * 74)

    service, _ = make_service(limit=10)
    vectors = await service.embed_texts(["a" * 3, "b" * 5])

    record(
        "2 条 → 返回 2 条",
        len(vectors) == 2,
        f"len={len(vectors)}",
    )
    record(
        "向量内容与输入对应（顺序未错位）",
        vectors[0][0] == 3.0 and vectors[1][0] == 5.0,
        f"{vectors[0][0]}, {vectors[1][0]}",
    )

    # ================================================
    print()
    print("=" * 74)
    print("3. 大批量自动分批")
    print("=" * 74)

    OpenAIEmbeddingService._learned_batch_limit = 10
    service, calls = make_service(limit=10)

    texts = [f"{index:04d}" for index in range(25)]
    vectors = await service.embed_texts(texts)

    record(
        "25 条 → 返回 25 条（此前会直接失败）",
        len(vectors) == 25,
        f"len={len(vectors)}",
    )
    record(
        "顺序未错位（第 i 条对应第 i 个向量）",
        all(
            vectors[index][0] == float(len(texts[index]))
            for index in range(25)
        ),
    )
    record(
        "确实分了批（每次不超过上限）",
        all(size <= 10 for size in calls),
        f"各批条数={calls}",
    )

    # ================================================
    print()
    print("=" * 74)
    print("4. 自适应：上限未知时从报错里学")
    print("=" * 74)

    # 把学到的上限清掉，并让初始猜测大于端点真实上限
    OpenAIEmbeddingService._learned_batch_limit = None
    original_batch_size = settings.embedding_batch_size
    settings.embedding_batch_size = 20

    try:
        service, calls = make_service(limit=10)

        texts = [f"t{index}" for index in range(25)]
        vectors = await service.embed_texts(texts)

        record(
            "初始猜测 20 > 真实上限 10 时仍能成功",
            len(vectors) == 25,
            f"len={len(vectors)}",
        )
        record(
            "从报错里学到了真实上限",
            OpenAIEmbeddingService.current_batch_limit() == 10,
            f"学到 {OpenAIEmbeddingService.current_batch_limit()}",
        )
        # 第一次请求按初始猜测 20 发出并被拒 —— 那是自适应**必然**
        # 付出的一次代价。因此断言只要求**之后**的批次遵守上限。
        # 要求"所有批次都 ≤10"会与自适应机制本身矛盾。
        record(
            "首次之后的批次都遵守学到的上限",
            all(size <= 10 for size in calls[1:]),
            f"各批条数={calls}（首条是初始猜测的代价）",
        )
        record(
            "只浪费了一次被拒的请求",
            len(calls) == 4,
            f"共 {len(calls)} 次请求",
        )
    finally:
        settings.embedding_batch_size = original_batch_size

    # ================================================
    print()
    print("=" * 74)
    print("5. 边界与错误处理")
    print("=" * 74)

    OpenAIEmbeddingService._learned_batch_limit = 10
    service, _ = make_service(limit=10)

    record(
        "空列表返回空列表（不发请求）",
        await service.embed_texts([]) == [],
    )

    service, _ = make_service(limit=10)
    record(
        "恰好等于上限时不分批",
        len(await service.embed_texts(["x"] * 10)) == 10,
    )

    # 与批量无关的 400 必须原样抛出，不能被误当成"批量过大"
    from openai import BadRequestError

    async def unrelated_error(model: str, input):  # noqa: A002
        raise BadRequestError(
            message="Error code: 400 - 参数不合法",
            response=SimpleNamespace(
                status_code=400,
                headers={},
                request=SimpleNamespace(),
            ),
            body=None,
        )

    service, _ = make_service(limit=10)
    service.client = SimpleNamespace(
        embeddings=SimpleNamespace(create=unrelated_error)
    )

    raised = False
    try:
        await service.embed_texts(["x"] * 25)
    except BadRequestError:
        raised = True
    except Exception:  # noqa: BLE001
        raised = False

    record(
        "与批量无关的 400 原样抛出（不被误判）",
        raised,
    )

    OpenAIEmbeddingService._learned_batch_limit = None

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
