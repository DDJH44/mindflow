"""用 LLM 对候选片段重排（MIND_FLOW_PLAN.md §8.5 的改进项）。

## 为什么需要它：基线数据直接指出了这个缺口

v3 基线（§8.5）：文档级 R@1 = 0.957，而 chunk 级 R@1 = **0.609**。

逐用例看那 9 例 R@1 失败，正确答案**几乎都进了 top-5**：

    命中位置=2  → 4 例
    命中位置=3  → 1 例
    命中位置=4  → 1 例
    命中位置=5  → 1 例
    None        → 1 例（唯一真正的召回失败）

也就是说：**问题不是"没找到"，而是"没排到第一"**。
`recall@5 = 0.957` 证明候选集基本够用，因此重排是直接对症的。

## 为什么不是"加相似度阈值"

那个方案已被测量否掉（见 `measure_retrieval_scores.py`）：

    | | 命中目标的分数 | 未命中的分数 |
    | 中位 | +0.5374 | +0.5209 |

**中位数只差 0.0165**，重叠区间几乎覆盖整个取值范围
（[0.4159, 0.6869]）。阈值 0.50 会滤掉 48 个噪声，
但同时**误伤 19 个正确答案** —— 那是拿召回换精确率，不是改进。

**根因是余弦分数在这套语料上缺乏区分度**，
因此必须换一个**更有区分度的判据** —— 这正是重排做的事：
它不看向量距离，而是让模型直接读"这段文字能不能回答这个问题"。

## 位置偏置的处理

LLM 对**列表位置**有系统性偏好（倾向选靠前的）。
因此候选**按打乱后的顺序**呈现，并把分数映射回 chunk_id ——
否则"重排"会把原来的顺序偏好换个形式保留下来。

用法：

    from app.services.rerank_service import RerankService

    reranker = RerankService()
    ordered = await reranker.rerank(query, candidates, top_k=5)
"""

import random

from pydantic import BaseModel, Field

from app.core.logging import get_logger
from app.core.model_json import (
    ModelJSONParseError,
    parse_model_json,
)
from app.services.openai_llm_service import OpenAILLMService

logger = get_logger("app.rerank")


class _Score(BaseModel):
    """单个候选的相关性打分。"""

    chunk_id: int = Field(description="候选片段的 id，必须原样返回")
    score: float = Field(
        description="相关性 0–10。10 = 直接回答了问题；0 = 完全无关"
    )


class _RerankResult(BaseModel):
    scores: list[_Score] = Field(default_factory=list)


SYSTEM_PROMPT = """你是检索结果的相关性评估器。

给你一个**问题**和若干**候选片段**，请为每个片段打 0–10 分，
表示"这段文字能在多大程度上回答这个问题"。

评分标准：

- **9–10**：直接、完整地回答了问题。包含问题所要的那个具体事实。
- **6–8**：包含相关事实，但不是问题直接问的那个。
- **3–5**：同一主题，但不回答问题。
- **0–2**：无关。

严格要求：

1. **不要把"同一份材料"当作相关。** 同一个文件里的其它段落
   往往主题相近却不回答问题 —— 那种给 3–5 分。
2. 判断依据是**问题所问的具体事实是否出现在这段文字里**，
   而不是文字与问题是否共享词汇。
3. 每个候选都要给分，`chunk_id` 必须原样返回。

只返回 JSON，不要解释：

{"scores": [{"chunk_id": 12, "score": 3.0}]}
"""


class RerankService:
    """按"能否回答问题"重新排序候选片段。"""

    # 送给模型时每个候选截断到多少字符。
    #
    # ⚠️ 这个值直接决定延迟：**输入长度是 LLM 调用耗时的主因**。
    # 实测 800 字符 × 10 个候选时，单次重排中位 **10.5 秒** ——
    # 而每次出题会调检索 4 次，那就是每道题多 42 秒等待。
    #
    # 截断到 300 字符的依据：判断"这段能不能回答这个问题"
    # 通常看开头就够（材料是按叙述顺序切的，关键事实一般在段首），
    # 而 300 字符仍能容纳 2–3 句完整表述。
    CONTENT_LIMIT = 300

    def __init__(self) -> None:
        self.llm_service = OpenAILLMService()

    async def rerank(
        self,
        query: str,
        candidates: list[dict],
        top_k: int = 5,
        seed: int | None = None,
        content_limit: int | None = None,
    ) -> list[dict]:
        """重排。返回前 `top_k` 个。

        **失败时回退到原顺序**，而不是抛异常或返回空 ——
        重排是增强步骤，它不该让整个检索失败。
        但会记 warning，否则"重排一直静默失效"不会被发现。
        """

        if len(candidates) <= 1:
            return candidates[:top_k]

        limit = (
            self.CONTENT_LIMIT
            if content_limit is None
            else content_limit
        )

        shuffled = list(candidates)

        # 打乱呈现顺序以削弱位置偏置。
        #
        # `seed` 让评估可复现 —— 否则同一份数据两次跑出的
        # 重排结果可能不同，而那种差异无法归因。
        random.Random(seed).shuffle(shuffled)

        listing = "\n\n".join(
            f"[chunk_id={item['chunk_id']}]\n{item['content'][:limit]}"
            for item in shuffled
        )

        prompt = (
            f"问题：{query}\n\n"
            f"候选片段（共 {len(shuffled)} 个）：\n\n{listing}"
        )

        try:
            raw = await self.llm_service.generate(
                prompt=prompt,
                system_prompt=SYSTEM_PROMPT,
                # 温度 0：相关性评估是判断题，不是创作。
                # 同一份输入两次给出不同排序会让评估不可复现。
                temperature=0.0,
                max_tokens=1500,
            )

            parsed = parse_model_json(raw, _RerankResult)

        except ModelJSONParseError as exc:
            logger.warning(
                "重排结果无法解析，回退到原顺序：%s", str(exc)[:150]
            )
            return candidates[:top_k]

        except Exception as exc:  # noqa: BLE001
            logger.warning(
                "重排调用失败，回退到原顺序：%s: %s",
                type(exc).__name__,
                str(exc)[:150],
            )
            return candidates[:top_k]

        scores = {
            item.chunk_id: item.score for item in parsed.scores
        }

        if not scores:
            logger.warning("重排返回了空的 scores，回退到原顺序")
            return candidates[:top_k]

        # 未被打分的候选排到最后（而不是当作 0）：
        # 漏打分说明模型没看完，而"当作 0"会把它和
        # "明确判为无关"混为一谈。
        def sort_key(index_and_item: tuple[int, dict]) -> tuple:
            index, item = index_and_item
            score = scores.get(item["chunk_id"])
            has_score = score is not None
            return (
                0 if has_score else 1,
                -(score if has_score else 0.0),
                index,  # 同分时保持原顺序，保证稳定
            )

        ordered = [
            item
            for _, item in sorted(
                enumerate(candidates), key=sort_key
            )
        ]

        # 把重排分数附在结果上，便于排查"为什么这样排"
        for item in ordered:
            score = scores.get(item["chunk_id"])
            if score is not None:
                item["rerank_score"] = score

        return ordered[:top_k]
