"""向量库不可用错误的识别与 503 映射。

## 它防的是什么（2026-10-20 实测事故）

Milvus 容器没在运行时，用户点击「开始面试」：

    create 会话 → 201（成功）
    start 生成首题 → 检索连 Milvus :19530 被拒
    → grpc._channel._InactiveRpcError 不命中任何 except
    → **裸 500 "Internal Server Error"**
    → 前端不跳转，用户只看到"没反应"

真实原因是"依赖不可用"（重启容器即可），但表现完全像代码 bug。
这与 D57 修 LLM 裸 500 是同一类问题：**依赖不可用必须与代码
错误可区分**，否则每个看日志的人都会去查错方向。

## 它验证

- 事故的真实形态：向未监听端口发起 gRPC 调用，捕获真实的
  `_InactiveRpcError` → 必须命中（不是靠模拟类名）
- pymilvus 自身的异常类名（MilvusException 等）命中
- 服务层把连接错误包进 RuntimeError（`__cause__` 链）也命中
- 普通代码错误（ValueError）**不**命中 —— 映射是加分项，
  误命中会把真缺陷伪装成"环境问题"
- LLM 错误与向量库错误判据互不越界
- 路由映射函数：命中 → 503 + 说明额度已退；不命中 → None

用法：uv run python -m tests.test_vector_store_errors
"""

import sys

from app.core.llm_errors import is_llm_error
from app.core.vector_store_errors import is_vector_store_error
from app.routers.interviews import _vector_store_error_or_none


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


def main() -> int:
    sys.path.insert(0, r"D:\yuxi\MindFlow\backend")

    result = Result()

    print("=" * 78)
    print("向量库错误识别与 503 映射验证")
    print("=" * 78)

    # ================================================
    print()
    print("① 事故的真实形态：gRPC 连不上的真实异常")
    # ================================================

    # 向未监听端口发起一元调用 —— 与"Milvus 容器没起"时
    # pymilvus 抛出的是**同一个类**。不用模拟类名：
    # 模拟验证不了"类名清单是否写对了真实类型"。
    import grpc

    channel = grpc.insecure_channel("127.0.0.1:1")
    call = channel.unary_unary("/test/method")

    real_error: BaseException | None = None

    try:
        call(b"")
    except Exception as exc:  # noqa: BLE001
        real_error = exc

    result.check(
        "未监听端口的 gRPC 调用确实抛了异常",
        real_error is not None,
        type(real_error).__name__ if real_error else "未抛",
    )

    if real_error is not None:
        result.check(
            "真实 _InactiveRpcError 被识别为向量库错误",
            is_vector_store_error(real_error),
            type(real_error).__name__,
        )
        result.check(
            "路由映射：命中 → 503",
            _vector_store_error_or_none(real_error) is not None
            and _vector_store_error_or_none(
                real_error
            ).status_code  # type: ignore[union-attr]
            == 503,
        )

    # ================================================
    print()
    print("② pymilvus 异常类名（服务端拒绝 / 不可用）")
    # ================================================

    # 动态造类：判据按类名匹配，不依赖 import pymilvus ——
    # 这也验证了"换向量库实现后判据仍工作"的设计意图。
    MilvusException = type("MilvusException", (Exception,), {})
    MilvusUnavailableException = type(
        "MilvusUnavailableException", (Exception,), {}
    )

    result.check(
        "MilvusException 命中",
        is_vector_store_error(MilvusException("x")),
    )
    result.check(
        "MilvusUnavailableException 命中",
        is_vector_store_error(MilvusUnavailableException("x")),
    )

    # ================================================
    print()
    print("③ 服务层包装（__cause__ 链）")
    # ================================================

    wrapped = RuntimeError("检索失败")
    wrapped.__cause__ = MilvusException("connection refused")

    result.check(
        "RuntimeError 包装的连接错误仍被识别",
        is_vector_store_error(wrapped),
    )

    # ================================================
    print()
    print("④ 不误伤：普通错误必须原样抛出")
    # ================================================

    result.check(
        "普通 ValueError 不命中（命中会掩盖真缺陷）",
        not is_vector_store_error(ValueError("代码 bug")),
    )
    result.check(
        "普通 RuntimeError（无链）不命中",
        not is_vector_store_error(RuntimeError("纯代码错误")),
    )
    result.check(
        "映射函数对普通错误返回 None（原样 raise）",
        _vector_store_error_or_none(ValueError("x")) is None,
    )

    # ================================================
    print()
    print("⑤ 与 LLM 判据互不越界")
    # ================================================

    APIConnectionError = type(
        "APIConnectionError", (Exception,), {}
    )

    result.check(
        "向量库错误不被 LLM 判据认走（文案会说错原因）",
        not is_llm_error(MilvusException("x")),
    )
    result.check(
        "LLM 连接错误不被向量库判据认走",
        not is_vector_store_error(APIConnectionError("x")),
    )

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
    sys.exit(main())
