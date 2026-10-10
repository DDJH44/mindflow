from collections import Counter

from app.core.logging import get_logger
from app.core.config import settings
from app.core.model_json import parse_model_json
from app.evaluation.interview.judges import dedupe_paraphrases
from app.schemas.interview.answer_analysis import AnswerAnalysis
from app.services.openai_llm_service import OpenAILLMService

logger = get_logger("app.analyzer")


# 采样策略：线上路径与离线评估分开配置。
#
# 为什么要分开：
# - 离线评估需要**可复现**，因此多次采样后按多数档位合并。
# - 线上答题时用户要等结果，每多采一次延迟就成倍增加
#   （实测 1 次约 9.5s、3 次约 27s）。
#   线上需要的是响应速度，可复现性由离线评估保证。
#
# ⚠️ 这意味着**线上分数比评估分数波动更大**：
# 单次采样在档位边界会跳档，实测同一输入极差可达 20-30 分。
# 因此**不要把线上分数与评估报告里的分数直接比较**，
# 详见 MIND_FLOW_PLAN.md §8A.10。
#
# 几次被数据推翻的推测记录在此，改动前务必先读：
# 1. 采样数取 5 曾被认为更稳，对照实验证明毫无改善（ADR-020）。
# 2. 采样数必须为奇数，否则平票时多数票退化为"偏向第一次采样"。
def online_analysis_samples() -> int:
    """线上答题路径使用的采样次数。"""

    return max(settings.online_analysis_samples, 1)


def offline_analysis_samples() -> int:
    """离线评估使用的采样次数。"""

    return max(settings.offline_analysis_samples, 1)


# 兼容别名：服务层默认走线上配置。
# 新代码请显式调用 online_analysis_samples() / offline_analysis_samples()
# 以表明自己属于哪条路径。
DEFAULT_SAMPLES = 1


# 评分 rubric。
#
# 设计要点：模型只选档位，不自由打分。
# 实测让模型自由填 0-100 时，同一输入的 answer_quality 极差可达 20 分
# （见 MIND_FLOW_PLAN.md §8A.5），分数不可复现。
# 锁定到 4 个锚点后，模型的主观漂移被压缩为"是否跳档"。
#
# 锚点描述刻意写成**与领域无关的质量特征**，
# 因为 analyzer 也会处理非技术类问题（如 HR 类追问）。
#
# ⚠️ 措辞原则（来自实测，见 §24.5 与 D44）：
#
# 1. **只用"存在性"判断，不用"计数"判断。**
#    原措辞写"A：至少两处具体信息""A：至少三项"，
#    实测模型对"数到几了"极不稳定，在 B/C 边界反复跳档。
#    改为"有/没有某类元素"。
# 2. **每档判据必须互斥且可核对。**
#    原 `depth` 的 C 档写"只提到技术名词"，
#    与总则"没有直接回应 → 最高 C""完全没有可用信息 → D"
#    在同一类回答上给出相反指示，导致 C/D 交替（实测 15/40 五五开）。
# 3. **程度词必须有可数替代。** "大部分要点""少部分要点"没有阈值，
#    改为"是否逐项回应了提问中的每个子问"。
RUBRIC = """
【判档总则】

每一步都只问"有没有"，不要估计"有多少"。
禁止使用"比较具体""部分覆盖""基本可以"这类程度判断。

**先定 quality，再用它约束 depth 与 completeness** ——
三者不是独立判断，顺序不能颠倒。

【第一步：回答 quality_anchor】

依次回答下面的问题，**第一个答"有/是"的就是结果**：

1. 回答里有没有**任何**能提取的实质内容？
   （数字、字段名、机制名、流程步骤、技术名词、对提问所问内容的任何说明）
   - 没有（只有寒暄、套话、"无可奉告"，或整段与提问完全无关）
     → **D**
   - 有 → 继续下一问
2. 有没有出现**具体的取值或数字**，且它直接对应提问所问的内容？
   （例如字符数、比例、条数、版本号、时间、维度）
   - 有 → **A**
3. 若没有数字：有没有出现**具体的机制或字段名称**，
   并说明它在这个场景下做什么用？
   （例如"按 chunk_id 回表取正文""用 project_id 过滤"）
   - 有 → **B**
   - 没有 → 继续下一问

   ⚠️ "具体机制"的界线：说的是**怎么做**，而不是**用了什么**。
   只说出组件或技术的名称（"用了 Redis""用了向量检索""做了缓存"）
   **不算**具体机制 —— 那是"用了什么"。
   必须说出可辨认的实现要素：字段、键、参数、策略名、处理步骤。
   例：「我用了 Redis 缓存高频数据」→ 不算（只是"用了什么"）；
   例：「用 TTL 让缓存半小时过期」「key 用 user_id 前缀」→ 算。
4. 若都没有：回答是否**只包含**态度、评价、方向性表述，
   或转向与提问所问无关的话题？
   （例如"要看具体情况""我们很重视""技术氛围很好"）
   - 是 → **C**
   - 否（确实在说明提问所问的内容，只是既无数字也无机制名）→ **B**

【第二步：技术深度 depth_anchor】

**只回答一个问题**：

> 这段回答里，有没有说明**某项技术是怎么运作的**
> （无论说得对不对、完不完整）？

- **有** → 它至少是 B：
  - 另外，回答里有没有出现**取舍或失败处理**？
    （例如"为什么选 500 而不选 1000""写入失败会重试"）
    - 有 → **A**
    - 没有 → **B**
- **没有** → 回答里有没有**提到任何技术名词**（组件、框架、协议名）？
  - 有 → **C**（提到了技术，但没解释它怎么运作）
  - 没有 → **D**（整段没有技术内容）

把"技术氛围很好""持续学习""大家很努力"这类表述视为**没有**技术名词。

⚠️ 注意：C 与 D 的区别**只在于有没有技术名词**，
与回答质量、是否切题无关。判断时不要考虑 quality，
只看这段文字里有没有出现技术名词。

【第三步：回答完整度 completeness_anchor】

先把提问拆成独立的子问，**逐个**检查回答里有没有**实质地说到**该子问。

"实质地说到" = 既在回应这个子问问的内容，
又给出了至少一个可核对的具体元素：
**一个数字、一个字段/机制名、或一个操作步骤**。
只有态度、评价、方向性表述（"要看情况""我们很重视"）**不算**说到。

设"实质说到的子问数"为 n，"子问总数"为 m：

- n = m（每个子问都实质说到）→ **A**
- 0 < n < m（至少一个实质说到，但没覆盖全部）→ **B**
- n = 0（一个都没实质说到）→ **C 或 D**：
  - 回答里有没有技术名词 → **C**
  - 没有 → **D**

⚠️ **提问只有一个子问时（m = 1）**，不要因为"提到了这个话题"就判 A。
若回答只是笼统地说"我用了 X""做过 Y"，没有给出任何数字、机制名或步骤，
那属于 **n = 0**，应当落到 C 或 D —— 覆盖了话题不等于说清了内容。

⚠️ 只数"有没有实质说到"，不要评价"说得好不好"。
一个子问只要有一个具体元素在说明它问的内容，就算说到了，
不要求说得完整或正确。

【特别说明：三者应当自洽】

正常情况下 depth 与 completeness 不应比 quality 更好。
若你算出 depth 或 completeness 比 quality 高，
请回头检查是不是把"没说到"误判成了"说到了"。

【可选微调】

只有在锚点描述与实际情况确实不完全吻合时才使用 adjusted_*：

- 必须是 0-100 的整数，且为 5 的倍数。
- 必须在 anchor_reason 中说明锚点哪里不吻合。
- **不确定就不要给 adjusted 值**，让系统使用锚点分。
- adjusted 只用于微调（通常与锚点分差距不超过 10 分）；
  如果需要调整到相邻档位，说明锚点选错了，应改成对应的锚点。
""".strip()


class InterviewAnswerAnalyzer:
    """分析候选人的面试回答"""

    SYSTEM_PROMPT = """
你是一名专业的技术面试官。

你的任务是分析候选人的面试回答。

请严格基于候选人提供的回答进行分析，不要编造候选人没有提到的经历、技术或事实。

请重点判断：

1. 回答是否切题。
2. 内容具体程度：是否给出了实现细节、参数取值、取舍理由。
3. 回答覆盖了问题要求的多少要点。
4. 候选人回答中有哪些明确体现出的优点。
5. 哪些信息没有说明清楚，值得进一步追问。
6. 用简洁的语言总结回答。

评分时必须使用给定的档位定义，不要自行发明评分标准。
请返回 JSON 格式，不要返回 Markdown，不要解释分析过程。
""".strip()

    def __init__(self):
        self.llm_service = OpenAILLMService()

    async def analyze(
        self,
        question: str,
        answer: str,
        seed: int | None = None,
        repeats: int = DEFAULT_SAMPLES,
    ) -> AnswerAnalysis:
        if not question.strip():
            raise ValueError("面试问题不能为空")

        if not answer.strip():
            raise ValueError("候选人回答不能为空")

        prompt = self._build_prompt(
            question=question,
            answer=answer,
        )

        # 多次采样。
        #
        # 实测单次采样时，处于档位边界的回答会随机跳档
        # （10 次里 9 次同档、1 次跳到相邻档），
        # 导致同一输入出现 20-30 分的极差。
        # 取多次采样后按分数求中位数，可以消除这种单次跳档。
        # 详见 MIND_FLOW_PLAN.md §8A.5 / §8A.9。
        samples: list[AnswerAnalysis] = []
        errors: list[str] = []

        for index in range(max(repeats, 1)):
            try:
                samples.append(
                    await self._analyze_once(
                        prompt=prompt,
                        seed=seed,
                        sample_index=index,
                    )
                )
            except Exception as exc:
                errors.append(f"{type(exc).__name__}: {exc}")
                logger.warning(
                    "第 %d/%d 次采样失败：%s: %s",
                    index + 1,
                    repeats,
                    type(exc).__name__,
                    exc,
                )

        if not samples:
            raise RuntimeError(
                "回答分析全部采样失败：" + " | ".join(errors)
            )

        if len(samples) == 1:
            # 单次采样也要记录，否则报告里无法区分
            # "只跑了一次" 和 "采样记录缺失"。
            samples[0].sample_count = 1
            samples[0].sampled_scores = [
                {
                    "answer_quality": samples[0].answer_quality,
                    "technical_depth": samples[0].technical_depth,
                    "completeness": samples[0].completeness,
                }
            ]
            # 保持与其他路径一致的字段语义：
            # 单样本时的"去重前缺口"就是它自己。
            samples[0].sampled_missing_points = [
                list(samples[0].missing_points)
            ]
            return samples[0]

        return _aggregate_samples(samples)

    async def _analyze_once(
        self,
        prompt: str,
        seed: int | None,
        sample_index: int,
    ) -> AnswerAnalysis:
        """执行一次采样并校验结果。"""

        result = await self.llm_service.generate(
            prompt=prompt,
            system_prompt=self.SYSTEM_PROMPT,
            temperature=0.2,
            # 分析结果含 missing_points 列表，
            # 实测单轮输出约 900-1500 字符，
            # 2000 tokens 在缺口较多时有截断风险。
            max_tokens=4000,
            seed=seed,
        )

        analysis = parse_model_json(result, AnswerAnalysis)

        # 防御：缺少锚点会让派生分数静默变成 0，
        # 也就是把"评分失败"伪装成"回答很差"。
        # 这是最危险的失败模式，必须显式报错而不是接受。
        if not analysis.has_anchor:
            raise ValueError(
                "模型未返回任何评分锚点，无法派生分数；"
                f"原始内容前 300 字符: {result[:300]!r}"
            )

        return analysis

    @staticmethod
    def _build_prompt(question: str, answer: str) -> str:
        return f"""
请分析以下面试问题和候选人的回答。

【面试问题】

{question}

【候选人回答】

{answer}

{RUBRIC}

请按照以下 JSON 结构返回：

{{
    "quality_anchor": "A | B | C | D",
    "depth_anchor": "A | B | C | D",
    "completeness_anchor": "A | B | C | D",
    "adjusted_quality_score": null,
    "adjusted_depth_score": null,
    "adjusted_completeness_score": null,
    "anchor_reason": "选择该档位的依据，一句话",
    "strengths": [],
    "missing_points": [],
    "summary": ""
}}

要求：

- 三个 anchor 字段必须是 "A"、"B"、"C"、"D" 之一，不能是别的值。
- 不要输出 answer_quality / technical_depth / completeness，
  这三个分数由系统根据 anchor 自动计算。
- adjusted_* 字段不确定时填 null，不要为了"精确"而随意填写。
- strengths 必须是字符串数组。
- missing_points 必须是字符串数组，只写回答里**没有**说明的信息。
- **missing_points 内部不要重复**：
  同一件事只能写一条。例如"未说明向量维度"与
  "未说明 Embedding 模型的向量维度选择依据"是同一件事，只能保留一条。
  写之前先自查：两条是否指向同一个技术点？是就合并。
- **missing_points 最多 6 条**，按重要性排序，只保留最值得追问的技术缺口；
  不要为了凑数把细枝末节也列出来。
- summary 必须是简洁的中文总结。
- 只能返回 JSON。
""".strip()


def _aggregate_samples(
    samples: list[AnswerAnalysis],
) -> AnswerAnalysis:
    """把多次采样合并为一个分析结果。

    - 分数：取各次的中位数（消除单次跳档）。
    - strengths / missing_points：取并集并去重，
      保留出现过的所有信息，不因某次遗漏而丢失。
    - summary 与 anchor_reason：取最长的一条，
      通常信息量最大。
    """

    anchors = [
        (
            sample.quality_anchor.value
            if sample.quality_anchor
            else "D"
        )
        for sample in samples
    ]
    depth_anchors = [
        (
            sample.depth_anchor.value
            if sample.depth_anchor
            else "D"
        )
        for sample in samples
    ]
    completeness_anchors = [
        (
            sample.completeness_anchor.value
            if sample.completeness_anchor
            else "D"
        )
        for sample in samples
    ]

    def majority(values: list[str]) -> str:
        """取出现次数最多的档位。

        并列时 Counter.most_common 返回先出现的那个，
        也就是偏向第一次采样。这是可接受的中性行为。
        """

        return Counter(values).most_common(1)[0][0]

    def longest(values: list[str]) -> str:
        non_empty = [value for value in values if value]
        return max(non_empty, key=len) if non_empty else ""

    return AnswerAnalysis(
        quality_anchor=majority(anchors),
        depth_anchor=majority(depth_anchors),
        completeness_anchor=majority(completeness_anchors),
        anchor_reason=longest(
            [sample.anchor_reason for sample in samples]
        ),
        strengths=dedupe_paraphrases(
            [sample.strengths for sample in samples]
        ),
        # 不能对多次采样的缺口直接取并集：
        # 同一件事会有多个同义说法，条数会从 6 条膨胀到 18 条，
        # 违反 §9.3「追问不重复」，也让报告无法阅读。
        # 因此按同义表述聚类去重，并优先保留多次提到的条目。
        missing_points=dedupe_paraphrases(
            [sample.missing_points for sample in samples]
        ),
        summary=longest(
            [sample.summary for sample in samples]
        ),
        sample_count=len(samples),
        sampled_scores=[
            {
                "answer_quality": sample.answer_quality,
                "technical_depth": sample.technical_depth,
                "completeness": sample.completeness,
            }
            for sample in samples
        ],
        sampled_missing_points=[
            list(sample.missing_points) for sample in samples
        ],
    )
