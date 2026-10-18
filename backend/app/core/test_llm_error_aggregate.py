"""验证上游失败在**聚合路径**上也被映射成 503（D75）。

## 缺陷

D57 已经把"单次 LLM 调用失败"映射成 503。但"回答分析"与
"整场评价"都会**多次采样**，全失败时抛的是**聚合错误**：

```python
raise RuntimeError("整场评价全部采样失败：" + " | ".join(errors))
```

这个 `RuntimeError` 的 MRO 里**没有** `OpenAIError` ——
原始类型只在消息文本里留下 `APITimeoutError` 几个字。
于是：

    上游超时 → 聚合 → 普通 RuntimeError → 映射不命中 → **裸 500**

客户端看到 "Internal Server Error"，分不清"上游抖动"
与"代码 bug"；而测试套件的 503 分流逻辑也**不会被触发**，
于是直接崩溃。

实测：门禁里 `test_interview_flow` / `test_paused_finish` /
`test_evaluation_fairness` 三个套件因此失败，
而失败信息是 `RuntimeError: ... APITimeoutError: Request timed out`
—— 看起来像代码回归，实际是上游抖动。

## 它验证五件事

1. 聚合路径抛的是 `UpstreamLLMError`，**不是**裸 `RuntimeError`
2. `UpstreamLLMError` **是** `RuntimeError` 的子类
   （既有 `except RuntimeError` 分支行为不变）
3. 映射函数把它转成 **503**
4. **超时与限流给出不同建议**（该等一会儿 vs 该降并发）
5. 普通业务异常**仍然不被**误映射成 503

用法：uv run python -m app.core.test_llm_error_aggregate
"""

import asyncio
import sys


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


class AlwaysFailingLLM:
    """每次调用都抛超时的假 LLM。

    用假实现而不是真的打网络：这个测试要验证的是
    "聚合后的错误类型"，不是上游是否可用。
    依赖真实上游会让它变成一个不稳定的测试。
    """

    async def generate(self, **kwargs):  # noqa: ANN003
        raise TimeoutError("模拟：上游超时")


async def main() -> int:
    sys.path.insert(0, r"D:\yuxi\MindFlow\backend")

    from app.core.llm_errors import UpstreamLLMError
    from app.routers.interviews import _llm_error_or_none
    from app.services.interview.answer_analyzer import (
        InterviewAnswerAnalyzer,
    )
    from app.services.interview.evaluator import InterviewEvaluator

    result = Result()

    print("=" * 78)
    print("上游失败聚合路径的映射验证（D75）")
    print("=" * 78)

    # ================================================
    print()
    print("=" * 78)
    print("① 聚合路径抛出的类型")
    print("=" * 78)

    # 回答分析：注入必失败的 LLM，repeats=2 让两次采样都失败。
    analyzer = InterviewAnswerAnalyzer()
    analyzer.llm_service = AlwaysFailingLLM()

    analyzer_error = None

    try:
        await analyzer.analyze(
            question="测试问题",
            answer="测试回答",
            repeats=2,
        )
    except Exception as exc:  # noqa: BLE001
        analyzer_error = exc

    result.check(
        "回答分析全失败 → 抛 UpstreamLLMError（不是裸 RuntimeError）",
        isinstance(analyzer_error, UpstreamLLMError),
        f"实得 {type(analyzer_error).__name__}",
    )

    # 整场评价：同样处理
    evaluator = InterviewEvaluator()
    evaluator.llm_service = AlwaysFailingLLM()

    evaluator_error = None

    try:
        await evaluator.evaluate(
            interview_content="问题：测试\n回答：测试",
            repeats=2,
        )
    except Exception as exc:  # noqa: BLE001
        evaluator_error = exc

    result.check(
        "整场评价全失败 → 抛 UpstreamLLMError",
        isinstance(evaluator_error, UpstreamLLMError),
        f"实得 {type(evaluator_error).__name__}",
    )

    # ================================================
    print()
    print("=" * 78)
    print("② 它必须**仍然是** RuntimeError（向后兼容）")
    print("=" * 78)

    # 既有代码里有 `except RuntimeError` 的分支（例如额度退还）。
    # 若基类换了，那些分支会**静默失效** —— 那是更糟的行为变更。
    result.check(
        "UpstreamLLMError 是 RuntimeError 的子类",
        issubclass(UpstreamLLMError, RuntimeError),
        "既有 except RuntimeError 分支不受影响",
    )

    # ================================================
    print()
    print("=" * 78)
    print("③ 映射成 503，且原因可区分")
    print("=" * 78)

    timeout_exc = UpstreamLLMError(
        "全部采样失败：APITimeoutError: Request timed out",
        cause_names=("APITimeoutError",),
    )
    mapped = _llm_error_or_none(timeout_exc)

    result.check(
        "聚合超时 → HTTP 503",
        mapped is not None and mapped.status_code == 503,
        f"实得 {getattr(mapped, 'status_code', None)}",
    )

    print(f"      超时文案：{mapped.detail if mapped else ''}")

    result.check(
        "超时文案说明「稍后重试」",
        mapped is not None and "超时" in str(mapped.detail),
    )

    rate_exc = UpstreamLLMError(
        "全部采样失败：RateLimitError", cause_names=("RateLimitError",)
    )
    mapped_rate = _llm_error_or_none(rate_exc)

    print(
        f"      限流文案：{mapped_rate.detail if mapped_rate else ''}"
    )

    result.check(
        "限流文案与超时**不同**（该做的事不同）",
        (
            mapped_rate is not None
            and mapped is not None
            and mapped_rate.detail != mapped.detail
            and "限流" in str(mapped_rate.detail)
        ),
    )

    # ================================================
    print()
    print("=" * 78)
    print("④ 普通业务异常不得被误映射成 503")
    print("=" * 78)

    # 这一条防的是"修过头"：把所有异常都说成"上游问题"，
    # 真实缺陷会被掩盖成"稍后重试"。
    for exc in (
        ValueError("业务错误"),
        KeyError("缺字段"),
        RuntimeError("普通运行时错误"),
    ):
        result.check(
            f"{type(exc).__name__} 不被映射成 503",
            _llm_error_or_none(exc) is None,
        )

    # ================================================
    print()
    print("=" * 78)
    print("⑤ 三处判断必须一致（防同一事实被写多处）")
    print("=" * 78)

    # 这个判断原本被写了两份：路由 `_llm_error_or_none` 与
    # `test_interview_flow._is_llm_error`。引入 UpstreamLLMError 时
    # 只更新了路由那份 —— 于是测试仍把它当普通异常并崩溃，
    # 失败信息看起来像代码回归。
    #
    # 现在统一走 `llm_errors.is_llm_error`。这一条断言两边结论相同,
    # 任何人都不能再悄悄分叉。
    from app.core.llm_errors import is_llm_error
    from app.core.test_interview_flow import _is_llm_error

    samples = [
        UpstreamLLMError("x", cause_names=("APITimeoutError",)),
        UpstreamLLMError("x", cause_names=("RateLimitError",)),
        ValueError("业务错误"),
        RuntimeError("普通错误"),
    ]

    disagreements = [
        f"{type(exc).__name__}: 统一={is_llm_error(exc)} "
        f"测试侧={_is_llm_error(exc)}"
        for exc in samples
        if is_llm_error(exc) != _is_llm_error(exc)
    ]

    result.check(
        "统一判据与测试侧判据结论一致",
        not disagreements,
        f"不一致 {disagreements}" if disagreements else "四处一致",
    )

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
