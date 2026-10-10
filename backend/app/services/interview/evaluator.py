from collections import Counter

from app.core.logging import get_logger
from app.core.config import settings
from app.core.model_json import parse_model_json
from app.schemas.interview.evaluation import (
    InterviewEvaluationResult,
)
from app.services.openai_llm_service import OpenAILLMService

logger = get_logger("app.evaluator")


# 整场评价的采样策略，与逐轮分析一致（见 answer_analyzer 的说明）。
# 线上路径与离线评估分开配置，理由是延迟与可复现性的需求不同。
def online_evaluation_samples() -> int:
    """线上整场评价使用的采样次数。"""

    return max(settings.online_evaluation_samples, 1)


def offline_evaluation_samples() -> int:
    """离线评估使用的采样次数。"""

    return max(settings.offline_evaluation_samples, 1)


# 兼容别名：服务层默认走线上配置。新代码请显式调用上面两个函数。
DEFAULT_SAMPLES = 1


# 整场评价 rubric。
#
# 与逐轮分析（AnswerAnalysis）的设计原则一致：
# 模型只选档位，不自由打分。
# 区别在于评价对象是**整场面试的多轮问答**，
# 因此档位描述强调"跨轮次的一致性与覆盖度"。
RUBRIC = """
【判档总则】

先判断"候选人是否正面回答了问题本身"，再判断"讲得够不够具体"。
两档之间拿不准时，选更差的那一档，并说明理由。

先回答一个问题：候选人的回答里，**有没有句子直接回应了提问要求的内容**？

- 有 → 至少是 B 档（继续看细节是否具体，决定 A 还是 B）。
- 没有，只有背景、态度、流程或方向性的表述 → 最高只能是 C 档。
- 完全没有可用信息或是答非所问 → D 档。

综合表现 overall_anchor：

- A：多数轮次都有句子直接回应，且至少两轮给出了可验证的具体信息
  （取值、步骤、字段、机制名称或实际发生过的处理）。
- B：多数轮次有直接回应，但具体信息只出现在少数轮次。
- C：多轮停留在概述、套话或回避；具体信息极少。
- D：基本没有可用信息，或多数轮次答非所问。

技术能力 technical_anchor（整场综合该维度）：

- A：说明过实现细节、参数取值、取舍理由或失败处理中的至少三项。
- B：说明过其中一到两项。
- C：只提到技术名词或方向，没有解释其运作方式。
- D：未体现任何技术信息。

项目能力 project_anchor：

- A：说明了本人在项目中的具体职责、技术方案与实现细节，
  并能解释为什么这样设计。
- B：说明了职责与技术方案，但缺少取舍理由或实现细节。
- C：只描述项目背景或团队成果，看不出本人做了什么。
- D：未涉及本人负责的部分。

沟通表达 communication_anchor：

- A：回答切题、有条理，能在追问中准确补充细节。
- B：回答基本切题，但组织性一般或追问后才说清。
- C：经常偏离问题，或表达混乱、需要反复追问。
- D：无法有效表达，回答与问题无关。

【可选微调】

只有在锚点描述与实际情况确实不完全吻合时才使用 adjusted_*：

- 必须是 0-100 的整数，且为 5 的倍数。
- 必须在 anchor_reason 中说明锚点哪里不吻合。
- **不确定就不要给 adjusted 值**，让系统使用锚点分。
- adjusted 只用于微调（通常与锚点分差距不超过 10 分）；
  如果需要调整到相邻档位，说明锚点选错了，应改成对应的锚点。
""".strip()


class InterviewEvaluator:
    """对一次完整面试进行综合评价"""

    SYSTEM_PROMPT = """
你是一名专业的技术面试官。

你的任务是根据候选人在一次完整面试中的问题和回答，
对候选人的表现进行综合评价。

请严格基于候选人实际提供的回答进行评价。

不要：

1. 编造候选人没有提到的技术经历。
2. 根据候选人的简历内容推测他一定掌握某项技术。
3. 因为候选人提到了某个技术名词，就默认其具备深入能力。
4. 使用与候选人回答无关的信息进行评价。

请重点评价：

1. 技术能力：技术概念理解、技术细节、原理理解、问题解决能力。
2. 项目能力：对项目的理解、对自己负责部分的理解、
   技术方案和实现细节、是否能够解释实际问题。
3. 沟通表达：是否切题、表达是否清晰、是否能够有条理地描述问题、
   是否能够准确回答追问。
4. 综合表现：综合考虑技术、项目和沟通表现。

评分时必须使用给定的档位定义，不要自行发明评分标准。
请返回 JSON 格式，不要返回 Markdown，不要解释分析过程。
""".strip()

    def __init__(self):
        self.llm_service = OpenAILLMService()

    async def evaluate(
        self,
        interview_content: str,
        seed: int | None = None,
        repeats: int = DEFAULT_SAMPLES,
    ) -> InterviewEvaluationResult:

        if not interview_content.strip():
            raise ValueError("面试内容不能为空")

        prompt = self._build_prompt(
            interview_content=interview_content,
        )

        samples: list[InterviewEvaluationResult] = []
        errors: list[str] = []

        for index in range(max(repeats, 1)):
            try:
                samples.append(
                    await self._evaluate_once(
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
                "整场评价全部采样失败：" + " | ".join(errors)
            )

        if len(samples) == 1:
            # 单次采样也要记录，否则无法区分
            # "只跑了一次" 与 "采样记录缺失"。
            samples[0].sample_count = 1
            samples[0].sampled_scores = [
                _scores_of(samples[0])
            ]
            return samples[0]

        return _aggregate_samples(samples)

    async def _evaluate_once(
        self,
        prompt: str,
        seed: int | None,
        sample_index: int,
    ) -> InterviewEvaluationResult:
        """执行一次采样并校验结果。"""

        result = await self.llm_service.generate(
            prompt=prompt,
            system_prompt=self.SYSTEM_PROMPT,
            temperature=0.2,
            # 整场评价的 strengths / weaknesses / suggestions
            # 实测各约 10-13 条，输出量明显大于逐轮分析，
            # 4000 tokens 会频繁触发 finish_reason=length。
            max_tokens=8000,
            seed=seed,
        )

        evaluation = parse_model_json(
            result,
            InterviewEvaluationResult,
        )

        # 防御：缺少锚点会让派生分数静默变成 0，
        # 也就是把"评价失败"伪装成"表现很差"。
        if not evaluation.has_anchor:
            raise ValueError(
                "模型未返回任何评分锚点，无法派生分数；"
                f"原始内容前 300 字符: {result[:300]!r}"
            )

        return evaluation

    @staticmethod
    def _build_prompt(interview_content: str) -> str:
        return f"""
请对下面这次完整面试进行综合评价。

【面试内容】

{interview_content}

{RUBRIC}

请按照以下 JSON 结构返回：

{{
    "overall_anchor": "A | B | C | D",
    "technical_anchor": "A | B | C | D",
    "project_anchor": "A | B | C | D",
    "communication_anchor": "A | B | C | D",
    "adjusted_overall_score": null,
    "adjusted_technical_score": null,
    "adjusted_project_score": null,
    "adjusted_communication_score": null,
    "anchor_reason": "选择该档位的依据，一句话",
    "strengths": [],
    "weaknesses": [],
    "suggestions": [],
    "feedback": ""
}}

要求：

- 四个 anchor 字段必须是 "A"、"B"、"C"、"D" 之一，不能是别的值。
- 不要输出 overall_score / technical_score / project_score /
  communication_score，这四个分数由系统根据 anchor 自动计算。
- adjusted_* 字段不确定时填 null，不要为了"精确"而随意填写。
- strengths、weaknesses、suggestions 必须是字符串数组。
- feedback 必须是简洁的中文总结。
- 所有评价必须基于候选人的实际回答。
- 只能返回 JSON。
""".strip()


def _scores_of(
    evaluation: InterviewEvaluationResult,
) -> dict:
    return {
        "overall_score": evaluation.overall_score,
        "technical_score": evaluation.technical_score,
        "project_score": evaluation.project_score,
        "communication_score": evaluation.communication_score,
    }


def _anchor_value(anchor) -> str:
    return anchor.value if anchor else "D"


def _aggregate_samples(
    samples: list[InterviewEvaluationResult],
) -> InterviewEvaluationResult:
    """把多次采样合并为一个评价结果。

    - 分数：取各次采样档位的多数（消除单次跳档）。
    - strengths / weaknesses / suggestions：取并集并去重，
      保留出现过的所有信息，不因某次遗漏而丢失。
    - feedback 与 anchor_reason：取最长的一条，通常信息量最大。
    """

    def merge_unique(items: list[list[str]]) -> list[str]:
        merged: list[str] = []
        for group in items:
            for item in group:
                if item not in merged:
                    merged.append(item)
        return merged

    def majority(values: list[str]) -> str:
        return Counter(values).most_common(1)[0][0]

    def longest(values: list[str]) -> str:
        non_empty = [value for value in values if value]
        return max(non_empty, key=len) if non_empty else ""

    return InterviewEvaluationResult(
        overall_anchor=majority(
            [_anchor_value(s.overall_anchor) for s in samples]
        ),
        technical_anchor=majority(
            [_anchor_value(s.technical_anchor) for s in samples]
        ),
        project_anchor=majority(
            [_anchor_value(s.project_anchor) for s in samples]
        ),
        communication_anchor=majority(
            [
                _anchor_value(s.communication_anchor)
                for s in samples
            ]
        ),
        anchor_reason=longest(
            [s.anchor_reason for s in samples]
        ),
        strengths=merge_unique(
            [s.strengths for s in samples]
        ),
        weaknesses=merge_unique(
            [s.weaknesses for s in samples]
        ),
        suggestions=merge_unique(
            [s.suggestions for s in samples]
        ),
        feedback=longest([s.feedback for s in samples]),
        sample_count=len(samples),
        sampled_scores=[_scores_of(s) for s in samples],
    )
