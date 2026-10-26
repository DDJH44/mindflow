from collections import Counter

from app.core.llm_errors import UpstreamLLMError
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

⚠️ **以上总则只适用于 `overall` / `technical` / `project` 三个维度。**

**`communication` 不受它约束** —— 它只看"表达得怎样"，
与"答出了多少技术内容"**无关**。理由见下方该维度的说明。

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

**这一维度只评价"表达本身"，不评价"内容水平"。**

判断依据仅限三件事：

1. 回答**是否切题**（有没有回应被问的那件事）
2. **是否有条理**（能不能听出层次，而不是散乱堆砌）
3. **是否在追问中能准确补充**（被问细了之后能否说清）

判断时**不得**因为"技术内容不足 / 答不出细节 / 不知道"而降低这一档。

- A：回答切题、有条理，能在追问中准确补充细节。
- B：回答基本切题，但组织性一般或追问后才说清。
- C：回答切题但过于简短、信息量低；或经常偏离问题；
  或表达混乱、需要反复追问。
- D：**无法有效表达**（语言不成句、逻辑无法跟随），
  或回答与问题**完全无关**（答的是另一件事，而不是"不知道"）。

⚠️ **明确说明「不知道」怎么判**（实测踩过）：

候选人直接说"不知道 / 没做过 / 不熟悉"，如果这句话
**切题且表达清楚**，那属于 **C 档，不是 D**：

- 它切题（清楚表明不会，没有跑题）
- 它表达清晰（不存在无法跟随的问题）
- 它只是在**内容**维度上不合格 —— 而那是
  `technical` / `project` / `overall` 应当反映的事

实测事故：一次 1 题面试中候选人答"不知道"，四个维度全是 D、
四项分数全是 15。前三项正确（确实无任何技术信息），
但**沟通给 D 是不公平的** —— D 档的定义是"无法有效表达"，
而"不知道"表达得好得很。

D 档**只保留**给真正的表达失败：
答非所问（问 A 答 B）、语言混乱无法跟随。

【可选微调】

只有在锚点描述与实际情况确实不完全吻合时才使用 adjusted_*：

- 必须是 0-100 的整数，且为 5 的倍数。
- 必须在 anchor_reason 中说明锚点哪里不吻合。
- **不确定就不要给 adjusted 值**，让系统使用锚点分。
- adjusted 只用于微调（通常与锚点分差距不超过 10 分）；
  如果需要调整到相邻档位，说明锚点选错了，应改成对应的锚点。

【样本量】

如果本次面试的**有效轮次很少**（例如只有 1–2 轮），
请在 anchor_reason 里**明确指出"本次样本轮次少"** ——
上述锚点描述都以"多数轮次"为口径，轮次不足时
结论的依据必然有限。不要给出看起来像"整场结论"的措辞。
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
        turn_count: int | None = None,
        max_turn: int | None = None,
    ) -> InterviewEvaluationResult:
        """评价一次完整面试。

        `turn_count`：**有效作答**轮次（有回答的题数）。
        传进来后，轮次很少时提示词会显式说明"样本量有限" ——
        否则 1 题的评价也会被说成"整场综合表现"。

        `max_turn`：提示词里**出现过的最大的轮次编号**（= 题目总数，
        含未作答的题）。

        ## 为什么两者必须分开（实测踩过）

        第一版只传了 `turn_count`，并用它做出处的上界校验。于是：

        - 提示词里第 2 轮是「未回答」，模型**看得到**它，因此引用第 2 轮
          是合法的；
        - 而 `turn_count` = 有效作答数 = 1，于是第 2 轮被判为越界丢弃；
        - 结果 **3 条结论全部 `verified` 为 False** —— 功能静默失效，
          报告上一个出处都不显示。

        两个数回答的是不同问题：
        - `turn_count`：这次评价的**样本**有多大（措辞要据此收敛）
        - `max_turn`：模型**能引用**的范围有多大（校验据此判定）

        省略时退化为 `turn_count`，保持旧调用方的行为不变。
        """

        if not interview_content.strip():
            raise ValueError("面试内容不能为空")

        prompt = self._build_prompt(
            interview_content=interview_content,
            turn_count=turn_count,
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
            # 抛**专门的类型**而不是裸 RuntimeError。
            #
            # 原因（D75）：裸 RuntimeError 的 MRO 里没有
            # `OpenAIError`，于是 `_llm_error_or_none` 的映射不命中，
            # 上游超时会变成裸 500 —— 客户端分不清"上游抖动"
            # 与"代码 bug"。实测就是三个套件因此崩溃。
            #
            # `UpstreamLLMError` 继承 RuntimeError，
            # 因此既有的 `except RuntimeError` 分支行为不变。
            raise UpstreamLLMError(
                "整场评价全部采样失败：" + " | ".join(errors),
                cause_names=tuple(
                    name.split(":")[0] for name in errors
                ),
            )

        # 出处校验：**必须在"单次采样提前返回"之前**。
        #
        # 位置踩过两次坑：
        # ① 第一版插在 `_evaluate_once` 里，而那个函数没有 `turn_count`
        #    参数 —— 编译通过、导入正常，只有真跑评价才抛 NameError，
        #    且被"采样失败"的日志包着，看起来像上游抖动。
        # ② 第二版插在 `if len(samples) == 1: return` **之后** ——
        #    而线上 `repeats=1`，于是校验在真实路径上**从不执行**：
        #    `verified` 保持默认 False，报告上一个出处都不显示。
        #    功能看起来"做了"，实际是死的。
        #
        # 教训：新逻辑要放在**所有出口之前**，否则线上路径可能绕过它。
        for sample_index, sample in enumerate(samples):
            before = len(sample.findings or [])

            sample.findings = _validate_findings(
                sample,
                # 上界用**题目总数**，不是有效作答数 ——
                # 见 evaluate 的 docstring：混用会让出处全部作废。
                turn_count=(
                    max_turn if max_turn is not None else turn_count
                ),
            )

            # 记录"给了 5 条、能用的只有 2 条"这类损耗。
            if before != len(sample.findings):
                logger.info(
                    "可追溯结论（第 %d 次采样）：给出 %d 条，"
                    "校验后保留 %d 条（未原样引用或轮次越界的已丢弃）",
                    sample_index + 1,
                    before,
                    len(sample.findings),
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

        try:
            evaluation = parse_model_json(
                result,
                InterviewEvaluationResult,
            )
        except Exception as exc:
            # 解析失败时必须把**模型实际输出了什么**记下来。
            #
            # 实测教训：门禁汇总里只显示
            # `1 validation error for InterviewEvaluationResult`，
            # 而**看不到模型到底返回了什么** —— 于是那条失败
            # 无法排查（重跑又通过了，属于偶发）。
            #
            # `parse_model_json` 的消息里带了原始内容的前 300 字符，
            # 但那部分在错误消息的**第二行**，
            # 而汇总通常只显示第一行。
            #
            # 因此完整输出记进 warning 日志：它是排查的唯一依据。
            logger.warning(
                "整场评价解析失败（第 %d 次采样）：%s: %s\n"
                "--- 模型原始输出（共 %d 字符）---\n%s",
                sample_index + 1,
                type(exc).__name__,
                str(exc)[:200],
                len(result),
                result[:2000],
            )
            raise

        # 防御：缺少锚点会让派生分数静默变成 0，
        # 也就是把"评价失败"伪装成"表现很差"。
        if not evaluation.has_anchor:
            raise ValueError(
                "模型未返回任何评分锚点，无法派生分数；"
                f"原始内容前 300 字符: {result[:300]!r}"
            )
        return evaluation

    @staticmethod
    def _build_prompt(
        interview_content: str,
        turn_count: int | None = None,
    ) -> str:
        """拼装整场评价的提示词。

        `turn_count` 是**有效作答轮次**（有回答的题数）。

        为什么要显式告诉模型：rubric 里的锚点描述都写的是
        "**多数轮次**都有直接回应"这类跨轮次口径，而模型此前
        **根本不知道这次评的是几轮** —— 于是 1 题的评价也会被说成
        "整场综合表现"，读起来像"你整体很差"，而实际样本量是 1。

        实测事故：一次 1 题的面试（候选人答"不知道"）得到
        "综合 15 分"，报告里没有任何"本次只有 1 轮"的提示。
        """

        sample_note = ""

        if turn_count is not None and turn_count <= 2:
            # 轮次很少时明确提示 —— 不让模型给出"整场结论"的措辞。
            sample_note = (
                f"\n【样本量提示】\n\n"
                f"本次面试只有 **{turn_count} 轮**有效作答。"
                f"上述锚点都按「多数轮次」的口径描述，"
                f"轮次这么少时结论的依据必然有限 —— "
                f"请在 anchor_reason 里明确指出这一点，"
                f"并避免使用「整场表现」「整体水平」这类措辞。\n"
            )

        return f"""
请对下面这次完整面试进行综合评价。
{sample_note}
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
    "feedback": "",
    "findings": [
        {{
            "kind": "strength | weakness | suggestion",
            "text": "结论本身，一句话",
            "turns": [1, 2]
        }}
    ]
}}

要求：

- 四个 anchor 字段必须是 "A"、"B"、"C"、"D" 之一，不能是别的值。
- 不要输出 overall_score / technical_score / project_score /
  communication_score，这四个分数由系统根据 anchor 自动计算。
- adjusted_* 字段不确定时填 null，不要为了"精确"而随意填写。
- strengths、weaknesses、suggestions 必须是字符串数组。
- feedback 必须是简洁的中文总结。
- 所有评价必须基于候选人的实际回答。
- **findings 是可追溯结论**：把 strengths / weaknesses /
  suggestions 里最关键的几条各自配上**依据的轮次编号**
  （面试内容里每轮都有「第 N 轮」标记）。
  - `text` 必须**逐字复制**上述三组里的某一条原文，不要改写 ——
    系统靠原文匹配把出处挂到对应条目上，改写会导致出处丢失。
  - `kind` 必须与那一组对应：strength / weakness / suggestion。
  - `turns` 只能填**真实存在的轮次**，不要编造。
  - 一条结论依据多轮时全部列出，例如 [1, 3]。
  - **真的找不到出处时填空数组 []**，不要为了凑数乱填 ——
    系统会校验，越界的轮次会被丢弃。
- 只能返回 JSON。
""".strip()


def _validate_findings(
    evaluation,
    turn_count: int | None,
) -> list:
    """校验结论的出处，返回清理后的列表。

    ## 规则

    1. **`text` 必须原样取自** `strengths` / `weaknesses` / `suggestions`
       之一（按 `kind` 对应）。不原样引用的一律丢弃。
    2. 轮次必须是整数且落在 `[1, turn_count]` 内 —— 其余丢弃。
       模型偶尔会给出 `0`、`5`（这轮不存在）、甚至 `"第2轮"` 这类字符串。
    3. 去重并排序，让 UI 的"第 1、3 轮"稳定。
    4. `verified` 由**本地**填写：只有"给了出处且全部合法"才是 True。
       让模型自己填等于让它自证。

    ## 为什么必须原样引用（这一条最容易做错）

    报告页要把出处挂在**那三组列表的条目上**。若模型把同一条结论
    改写了几个字，UI 就匹配不上 —— 结果是要么丢出处，要么用
    模糊匹配，而模糊匹配会**把出处挂到错误的条目上**（那比没有更糟）。

    因此宁可不给，也不猜。

    实测动机：第一版只校验了轮次范围，没有校验文本一致性。
    那会让"模型写了 5 条 findings，UI 只挂上 2 条"这种静默损耗
    无从发现 —— 现在不匹配就丢弃并记数（见 `evaluate` 的日志）。

    ## 为什么越界就丢弃而不是夹取到范围内

    夹取（把第 5 轮压成第 3 轮）会**伪造出处**：用户点过去看到的是
    另一轮，而那一轮可能根本没有这个问题。丢弃 + `verified=False`
    是诚实的：这条结论没找到可靠出处。

    `turn_count` 为 None 时不做范围校验（调用方没给轮数），
    此时仍过滤非正整数的轮次 —— 那些一定不合法。
    """

    # kind → 允许引用的原文列表
    allowed: dict[str, set[str]] = {
        "strength": {
            " ".join(str(item).split())
            for item in (evaluation.strengths or [])
        },
        "weakness": {
            " ".join(str(item).split())
            for item in (evaluation.weaknesses or [])
        },
        "suggestion": {
            " ".join(str(item).split())
            for item in (evaluation.suggestions or [])
        },
    }

    cleaned: list = []
    seen: set[tuple[str, str]] = set()

    for finding in evaluation.findings or []:
        text = " ".join(str(getattr(finding, "text", "")).split())

        if not text:
            continue

        kind = str(getattr(finding, "kind", "") or "").strip().lower()

        # 未知种类无法归类，也一定不在上面三组里
        if kind not in allowed:
            continue

        # 必须原样来自对应的那一组
        if text not in allowed[kind]:
            continue

        raw_turns = getattr(finding, "turns", None) or []
        valid_turns: list[int] = []

        for value in raw_turns:
            # 只接受真正的整数：`True` 在 Python 里是 int 的子类，
            # 必须显式排除，否则 `[true]` 会被当成第 1 轮。
            #
            # 纯数字字符串（`"2"`）也接受 —— 模型偶尔会把数字加引号。
            # 但 `"第2轮"` 这类**不**做正则抽取：那等于替模型猜它的意思，
            # 猜错就会把出处挂到错误的轮次上。
            if isinstance(value, bool):
                continue

            if isinstance(value, str):
                stripped = value.strip()

                if not stripped.isdigit():
                    continue

                value = stripped

            try:
                index = int(value)
            except (TypeError, ValueError):
                continue

            if index < 1:
                continue

            if turn_count is not None and index > turn_count:
                continue

            if index not in valid_turns:
                valid_turns.append(index)

        key = (kind, text)

        if key in seen:
            continue

        seen.add(key)

        cleaned.append(
            finding.model_copy(
                update={
                    "kind": kind,
                    "text": text,
                    "turns": sorted(valid_turns),
                    "verified": bool(valid_turns),
                }
            )
        )

    return cleaned


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
    - findings：按 (kind, text) 合并，出处取**交集**。
    """

    def merge_unique(items: list[list[str]]) -> list[str]:
        merged: list[str] = []
        for group in items:
            for item in group:
                if item not in merged:
                    merged.append(item)
        return merged

    def merge_findings(groups: list[list]) -> list:
        """合并多轮采样的结论。

        出处取**交集**而不是并集 —— 理由是"出处要对用户负责"：

        - 若两次采样都说"技术深度不足"但指的轮次不同，
          说明**它们自己都没把握**。此时并集会把两轮都标上，
          用户点过去大概率有一轮看不到问题 —— 那比少给一个
          出处更糟。
        - 交集为空时保留该结论但不带出处（`verified=False`），
          界面据此显示"未标注出处"，不伪装成可追溯。

        单次采样（线上默认）时交集就是它自己的出处，行为不变。
        """

        merged: dict[tuple[str, str], list] = {}

        for group in groups:
            for finding in group:
                key = (finding.kind, finding.text)

                if key not in merged:
                    merged[key] = finding
                    continue

                previous = merged[key]
                agreed = [
                    turn
                    for turn in previous.turns
                    if turn in finding.turns
                ]

                merged[key] = previous.model_copy(
                    update={
                        "turns": sorted(agreed),
                        "verified": bool(agreed),
                    }
                )

        return list(merged.values())

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
        findings=merge_findings([s.findings for s in samples]),
        sample_count=len(samples),
        sampled_scores=[_scores_of(s) for s in samples],
    )
