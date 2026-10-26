"""整场面试评价的输出契约。

与逐轮分析（AnswerAnalysis）保持一致，采用"锚点 + 服务端派生"，
而不是让模型自由填 0-100：

1. 模型只能选择预定义的等级锚点（A/B/C/D）。
2. 模型可选地给出 adjusted_*_score（0-100，5 的倍数并提供理由）。
3. 若未给出 adjusted_*_score，最终分由 ANCHOR_SCORES 直接派生。

原因：实测自由打分时同一场的分数不可复现
（见 MIND_FLOW_PLAN.md §8A.5）。整场评价原本仍为自由打分，
是评分不可复现的最后一个缺口，此处统一。
"""

from enum import Enum

from pydantic import BaseModel, Field, model_validator


class OverallAnchor(str, Enum):
    """综合表现锚点。"""

    A_EXCELLENT = "A"
    B_GOOD = "B"
    C_WEAK = "C"
    D_POOR = "D"


class TechnicalAnchor(str, Enum):
    """技术能力锚点。"""

    A_EXCELLENT = "A"
    B_GOOD = "B"
    C_WEAK = "C"
    D_POOR = "D"


class ProjectAnchor(str, Enum):
    """项目能力锚点。"""

    A_EXCELLENT = "A"
    B_GOOD = "B"
    C_WEAK = "C"
    D_POOR = "D"


class CommunicationAnchor(str, Enum):
    """沟通表达锚点。"""

    A_EXCELLENT = "A"
    B_GOOD = "B"
    C_WEAK = "C"
    D_POOR = "D"


# 锚点到分数的映射。
# 与 AnswerAnalysis.ANCHOR_SCORES 保持一致，
# 避免同一套档位在逐轮与整场两处给出不同分数。
ANCHOR_SCORES: dict[str, int] = {
    "A": 90,
    "B": 70,
    "C": 40,
    "D": 15,
}

ANCHOR_LABELS: dict[str, str] = {
    "A": "优秀",
    "B": "良好",
    "C": "偏弱",
    "D": "很差",
}


class Finding(BaseModel):
    """一条**可追溯**的结论。

    ## 为什么需要它

    报告此前只有三组字符串：优势、不足、建议。它们读起来像结论，
    但**没有出处** —— 用户问"你凭什么说我技术深度不够"，
    界面答不出来。

    而可追溯性对"练习"这件事是刚需：一句"回答缺少取舍依据"
    如果不能指到具体哪一轮，用户只能把这句评价当成主观印象。

    ## 为什么让模型给出处，而不是事后用相似度匹配

    事后匹配（把结论文本和每轮回答做语义相似度）看起来省事，
    但它会**编造归属**：两条无关的文本也会算出一个"最相似"，
    而那正是本项目在弱点聚合里已经明确拒绝的做法
    （见 `ability_profile_service` 的"弱点不做自动归类"）。

    模型在生成结论时**确实知道**它指的是哪一轮。让它直接报轮次，
    再由本地**严格校验**（超范围、非数字一律丢弃），
    比事后猜可靠得多。

    ## `verified` 的含义

    - `True`：模型给了轮次，且**全部**在有效范围内
    - `False`：模型没给，或给的轮次越界（已丢弃）

    UI 必须区分显示：把"模型没标出处"的结论渲染成带出处的样子，
    等于伪造可信度。
    """

    kind: str = Field(
        description="strength / weakness / suggestion 之一",
    )
    text: str = Field(description="结论本身，一句话")
    # ⚠️ 刻意**不加** `list[int]` 类型约束。
    #
    # 实测后果：约束成 `list[int]` 时，模型只要把轮次写成
    # `"第2轮"`，Pydantic 就会在**解析阶段**抛
    # `int_parsing` 错误 —— 而那是**整份评价**的解析失败，
    # 于是会话卡在 evaluating（正是 D76 那个场景）：
    # 一条脏的出处，毁掉整场评价。
    #
    # 因此类型放宽，由 `_validate_findings` 逐值过滤：
    # 非整数、布尔、越界一律丢弃，且**不影响其它字段**。
    # 宽容解析 + 严格校验，比严格解析 + 全盘失败更合适这里。
    turns: list = Field(
        default_factory=list,
        description="该结论依据的轮次编号（从 1 开始）",
    )
    verified: bool = Field(
        default=False,
        description="出处是否有效（由本地校验填写，模型不应伪造）",
    )


class InterviewEvaluationResult(BaseModel):
    """一次完整面试的评价结果。

    四个分数由服务端根据锚点派生，模型不需要（也不应该）直接填写。
    """

    overall_score: int = Field(
        default=0,
        ge=0,
        le=100,
        description="综合表现评分，由 overall_anchor 派生",
    )

    technical_score: int = Field(
        default=0,
        ge=0,
        le=100,
        description="技术能力评分，由 technical_anchor 派生",
    )

    project_score: int = Field(
        default=0,
        ge=0,
        le=100,
        description="项目能力评分，由 project_anchor 派生",
    )

    communication_score: int = Field(
        default=0,
        ge=0,
        le=100,
        description="沟通表达能力评分，由 communication_anchor 派生",
    )

    # ---------------- 锚点（模型需要填的字段） ----------------

    overall_anchor: OverallAnchor | None = Field(
        default=None,
        description="综合表现等级",
    )

    technical_anchor: TechnicalAnchor | None = Field(
        default=None,
        description="技术能力等级",
    )

    project_anchor: ProjectAnchor | None = Field(
        default=None,
        description="项目能力等级",
    )

    communication_anchor: CommunicationAnchor | None = Field(
        default=None,
        description="沟通表达等级",
    )

    # ---------------- 可选微调 ----------------

    adjusted_overall_score: int | None = Field(
        default=None,
        ge=0,
        le=100,
        description="可选：综合表现的调整分，必须是 5 的倍数",
    )

    adjusted_technical_score: int | None = Field(
        default=None,
        ge=0,
        le=100,
        description="可选：技术能力的调整分",
    )

    adjusted_project_score: int | None = Field(
        default=None,
        ge=0,
        le=100,
        description="可选：项目能力的调整分",
    )

    adjusted_communication_score: int | None = Field(
        default=None,
        ge=0,
        le=100,
        description="可选：沟通表达的调整分",
    )

    anchor_reason: str = Field(
        default="",
        description="选择该档位的依据，一句话",
    )

    # ---------------- 原有内容字段 ----------------

    strengths: list[str] = Field(
        default_factory=list,
        description="候选人的主要优势",
    )

    weaknesses: list[str] = Field(
        default_factory=list,
        description="候选人的主要薄弱点",
    )

    suggestions: list[str] = Field(
        default_factory=list,
        description="针对候选人的改进建议",
    )

    feedback: str = Field(
        default="",
        description="对本次面试表现的总体评价",
    )

    findings: list[Finding] = Field(
        default_factory=list,
        description=(
            "可追溯的结论（优势/不足/建议各带出处轮次）。"
            "模型负责给出处，`verified` 由本地校验填写"
        ),
    )

    # ---------------- 采样记录 ----------------

    sample_count: int = Field(
        default=0,
        description="实际成功的采样次数",
    )

    sampled_scores: list[dict] = Field(
        default_factory=list,
        description="每次采样的四项分数",
    )

    @model_validator(mode="after")
    def _derive_scores(self) -> "InterviewEvaluationResult":
        """由锚点派生最终分数。

        优先使用 adjusted_*_score（若提供），否则使用锚点分。
        adjusted 值若非 5 的倍数，规整到最近的 5 的倍数。
        """

        def resolve(
            anchor: str | None,
            adjusted: int | None,
        ) -> int:
            if adjusted is not None:
                return int(round(adjusted / 5) * 5)

            if anchor is not None:
                return ANCHOR_SCORES[anchor]

            return 0

        self.overall_score = resolve(
            self.overall_anchor.value
            if self.overall_anchor
            else None,
            self.adjusted_overall_score,
        )
        self.technical_score = resolve(
            self.technical_anchor.value
            if self.technical_anchor
            else None,
            self.adjusted_technical_score,
        )
        self.project_score = resolve(
            self.project_anchor.value
            if self.project_anchor
            else None,
            self.adjusted_project_score,
        )
        self.communication_score = resolve(
            self.communication_anchor.value
            if self.communication_anchor
            else None,
            self.adjusted_communication_score,
        )

        return self

    @property
    def has_anchor(self) -> bool:
        """是否至少提供了一个锚点。"""

        return any(
            anchor is not None
            for anchor in (
                self.overall_anchor,
                self.technical_anchor,
                self.project_anchor,
                self.communication_anchor,
            )
        )

    def anchor_summary(self) -> dict:
        """返回锚点与派生分数的对照，便于排查评分异常。"""

        def entry(anchor, adjusted: int | None, score: int) -> dict:
            return {
                "anchor": anchor.value if anchor else None,
                "label": (
                    ANCHOR_LABELS[anchor.value] if anchor else None
                ),
                "adjusted": adjusted,
                "score": score,
            }

        return {
            "overall": entry(
                self.overall_anchor,
                self.adjusted_overall_score,
                self.overall_score,
            ),
            "technical": entry(
                self.technical_anchor,
                self.adjusted_technical_score,
                self.technical_score,
            ),
            "project": entry(
                self.project_anchor,
                self.adjusted_project_score,
                self.project_score,
            ),
            "communication": entry(
                self.communication_anchor,
                self.adjusted_communication_score,
                self.communication_score,
            ),
            "anchor_reason": self.anchor_reason,
        }
