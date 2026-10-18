"""上游模型的可用性异常。

## 为什么需要专门的类型（D75）

D57 已经做了一次映射：把上游 LLM 的失败转成 **503**，而不是
裸 500。判据是异常的 `__mro__` 里有没有 `OpenAIError` 之类的名字。

但实测发现有一条路径**绕过了它**：

「回答分析」与「整场评价」都采用**多次采样**，且任一次失败
只记录、不中断。当**所有**采样都失败时，它们抛出的是一个
**聚合错误**：

```python
raise RuntimeError("整场评价全部采样失败：" + " | ".join(errors))
```

这个 `RuntimeError` 的 MRO 里**没有** `OpenAIError` ——
原始异常类型只在**消息文本**里留下了 `APITimeoutError` 这几个字，
类型信息已经丢失。

于是：上游超时 → 聚合 → 普通 `RuntimeError` → 映射不命中
→ **裸 500**。而客户端与测试套件看到的是
"Internal Server Error"，无法区分"上游抖动"与"代码 bug"。

实测后果：门禁里 `test_interview_flow` / `test_paused_finish` /
`test_evaluation_fairness` 三个套件以
`RuntimeError: ... APITimeoutError: Request timed out` 崩溃，
而它们的 503 分流逻辑根本没被触发。

## 修法：让聚合错误带上"这是上游问题"的语义

`UpstreamLLMError` **继承 `RuntimeError`** —— 这是刻意的：

- 既有代码里有 `except RuntimeError` 的分支，改成别的基类会
  让那些分支悄悄失效（那又是一个静默行为变更）
- 新增的类型名让 `_llm_error_or_none` 能准确识别它

也就是说：**对旧调用方行为不变，对新映射可识别。**
"""


class UpstreamLLMError(RuntimeError):
    """上游模型服务不可用（超时、限流、连接失败）。

    由"多次采样全部失败"的聚合路径抛出。

    `cause_names` 记录**原始异常的类型名**（例如 `APITimeoutError`）——
    类型信息在聚合时本会丢失，而排查时它是最有用的线索：
    "超时"和"限流"该做的事完全不同（等一会儿 vs 降并发）。
    """

    def __init__(
        self,
        message: str,
        *,
        cause_names: tuple[str, ...] = (),
    ):
        super().__init__(message)
        self.cause_names = cause_names

    def describe(self) -> str:
        """给用户看的一句话。

        文案规则（UI_DESIGN.md §9.2）：**能写具体就写**，
        不用"服务异常"这种空话。

        ⚠️ 这里用 `any(...in name...)` 而不是
        `"APITimeout" in self.cause_names` —— 后者是**元组的精确匹配**，
        而 `('APITimeoutError',)` 并不包含字符串 `'APITimeout'`。
        实测踩过：两种原因都落到通用文案，用户看不出该等一会儿
        还是该降并发。
        """

        joined = " ".join(self.cause_names)

        if "APITimeout" in joined:
            return (
                "调用模型超时。上游服务响应较慢，稍后重试即可，"
                "本次不消耗额度。"
            )

        if "RateLimit" in joined:
            return (
                "调用模型被限流。上游服务暂时拒接请求，"
                "请等一会儿再试，本次不消耗额度。"
            )

        return (
            "调用模型失败。这是上游模型服务的临时问题，"
            "稍后重试即可，本次不消耗额度。"
        )


# 上游 LLM 异常的类名特征。
#
# 用**类名与 MRO** 而不是 `import openai`：本项目允许替换 LLM 实现
# （§24 换过端点），硬绑具体 SDK 会让判断在换实现后失效。
LLM_ERROR_NAMES = frozenset(
    {
        "OpenAIError",
        "APITimeoutError",
        "APIConnectionError",
        "APIStatusError",
        "RateLimitError",
    }
)


def is_llm_error(exc: BaseException) -> bool:
    """判断异常是否表示"上游 LLM 不可用"。

    ## 为什么要有这个函数（而不是各处自己判断）

    这个判断原本被**写了两份**：路由里的 `_llm_error_or_none`
    与 `test_interview_flow._is_llm_error`，各自维护一份异常名清单。

    实测后果：引入 `UpstreamLLMError`（聚合路径专用）时只更新了
    路由那一份 —— 于是测试仍把它当普通异常并以 exit=1 崩溃，
    而失败信息看起来像代码回归。

    **同一个事实存在两处，改一处必然漏另一处。**
    因此判断集中到这里，两边都调它。

    ## 判据顺序

    1. `UpstreamLLMError` —— 聚合路径的专用类型，直接命中
    2. 自身 MRO 里的类名
    3. `__cause__` / `__context__` 链（有些包装异常只在链里
       保留原始类型）
    """

    if isinstance(exc, UpstreamLLMError):
        return True

    if {cls.__name__ for cls in type(exc).__mro__} & LLM_ERROR_NAMES:
        return True

    for chained in (exc.__cause__, exc.__context__):
        if chained is None:
            continue

        if isinstance(chained, UpstreamLLMError):
            return True

        if (
            {cls.__name__ for cls in type(chained).__mro__}
            & LLM_ERROR_NAMES
        ):
            return True

    return False
