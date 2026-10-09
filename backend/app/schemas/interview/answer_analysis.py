"""面试回答分析的输出契约。

评分采用"锚点 + 服务端派生"的方式，而不是让模型自由填 0-100：

1. 模型只能选择预定义的等级锚点（A/B/C/D）。
2. 模型可选地给出 adjusted_*_score（0-100，5 的倍数并提供理由），
   用于处理锚点描述与实际情况不完全吻合的边缘情况。
3. 若未给出 adjusted_*_score，最终分由 ANCHOR_SCORES 直接派生。

这样做的原因：
实测让模型自由打分时，同一输入的 answer_quality 极差可达 20 分
（详见 MIND_FLOW_PLAN.md §8A.5），分数不可复现。
锁定到 4 个锚点后，模型的主观漂移被压缩为"是否跳档"。
"""

from enum import Enum

from pydantic import BaseModel, Field, model_validator


class QualityAnchor(str, Enum):
    """回答整体质量的锚点。"""

    A_EXCELLENT = "A"
    B_GOOD = "B"
    C_WEAK = "C"
    D_POOR = "D"


class DepthAnchor(str, Enum):
    """技术深度的锚点。"""

    A_EXCELLENT = "A"
    B_GOOD = "B"
    C_WEAK = "C"
    D_POOR = "D"


class CompletenessAnchor(str, Enum):
    """回答完整度的锚点。"""

    A_EXCELLENT = "A"
    B_GOOD = "B"
    C_WEAK = "C"
    D_POOR = "D"


# 锚点到分数的映射。
# 这是评分的**唯一事实源**：模型不直接产生分数。
#
# 为什么是 4 档而不是 5 档：
# 实测 5 档（把 B 拆成 B+ 与 B）时，模型在 quality 与 depth 上
# **一次都没有选择 B+**，仍在 C 与 B 之间二选一，
# 平均标准差反而从 1.79 升到 1.92（见 MIND_FLOW_PLAN.md §8A.5）。
# 增加档位只让档位表更复杂，没有降低跳档幅度，因此回退为 4 档。
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


class AnswerAnalysis(BaseModel):
    """候选人回答分析结果。

    answer_quality / technical_depth / completeness 三个分数
    由服务端根据锚点派生，模型不需要（也不应该）直接填写。
    """

    answer_quality: int = Field(
        default=0,
        ge=0,
        le=100,
        description="回答整体质量，由 quality_anchor 派生",
    )

    technical_depth: int = Field(
        default=0,
        ge=0,
        le=100,
        description="技术深度，由 depth_anchor 派生",
    )

    completeness: int = Field(
        default=0,
        ge=0,
        le=100,
        description="回答完整度，由 completeness_anchor 派生",
    )

    # ---------------- 锚点（模型需要填的字段） ----------------

    quality_anchor: QualityAnchor | None = Field(
        default=None,
        description="回答整体质量等级",
    )

    depth_anchor: DepthAnchor | None = Field(
        default=None,
        description="技术深度等级",
    )

    completeness_anchor: CompletenessAnchor | None = Field(
        default=None,
        description="回答完整度等级",
    )

    # ---------------- 可选微调 ----------------

    adjusted_quality_score: int | None = Field(
        default=None,
        ge=0,
        le=100,
        description=(
            "可选：当锚点描述与实际情况不完全吻合时的调整分，"
            "必须是 5 的倍数，且需在 anchor_reason 中说明理由"
        ),
    )

    adjusted_depth_score: int | None = Field(
        default=None,
        ge=0,
        le=100,
        description="可选：技术深度的调整分",
    )

    adjusted_completeness_score: int | None = Field(
        default=None,
        ge=0,
        le=100,
        description="可选：完整度的调整分",
    )

    anchor_reason: str = Field(
        default="",
        description="选择该锚点档位的依据，一句话",
    )

    # ---------------- 原有字段 ----------------

    strengths: list[str] = Field(
        default_factory=list,
        description="回答中的优点",
    )

    missing_points: list[str] = Field(
        default_factory=list,
        description="回答中缺失或需要进一步确认的信息",
    )

    summary: str = Field(
        default="",
        description="对候选人回答的简要总结",
    )

    # 采样过程记录。
    # 分析器默认采样多次以消除档位边界跳档，
    # 这里保留每次采样的分数与缺口，便于排查
    # "去重是否生效" 与 "最终条数是否被上限截断"。
    sample_count: int = Field(
        default=0,
        description="实际成功的采样次数",
    )

    sampled_scores: list[dict] = Field(
        default_factory=list,
        description="每次采样的三项分数",
    )

    sampled_missing_points: list[list[str]] = Field(
        default_factory=list,
        description=(
            "去重前，每次采样各自报出的缺口。"
            "用于诊断同义去重是否生效，以及最终条数是否被上限截断"
        ),
    )

    sampled_missing_points: list[list[str]] = Field(
        default_factory=list,
        description=(
            "去重前，每次采样各自报出的缺口。"
            "用于诊断同义去重是否生效，以及最终条数是否被上限截断"
        ),
    )

    @model_validator(mode="after")
    def _derive_scores(self) -> "AnswerAnalysis":
        """由锚点派生最终分数。

        优先使用 adjusted_*_score（若提供），否则使用锚点分。
        adjusted 值若非 5 的倍数，会被规整到最近的 5 的倍数，
        避免模型给出 73 这类无法解释的精度。
        """

        def resolve(
            anchor: str | None,
            adjusted: int | None,
        ) -> int:
            if adjusted is not None:
                return int(round(adjusted / 5) * 5)

            if anchor is not None:
                return ANCHOR_SCORES[anchor.value]

            return 0

        self.answer_quality = resolve(
            self.quality_anchor,
            self.adjusted_quality_score,
        )
        self.technical_depth = resolve(
            self.depth_anchor,
            self.adjusted_depth_score,
        )
        self.completeness = resolve(
            self.completeness_anchor,
            self.adjusted_completeness_score,
        )

        return self

    @property
    def has_anchor(self) -> bool:
        """是否至少提供了一个锚点。"""

        return any(
            anchor is not None
            for anchor in (
                self.quality_anchor,
                self.depth_anchor,
                self.completeness_anchor,
            )
        )

    def anchor_summary(self) -> dict:
        """返回锚点与派生分数的对照，便于排查评分异常。"""

        return {
            "quality": {
                "anchor": (
                    self.quality_anchor.value
                    if self.quality_anchor
                    else None
                ),
                "label": (
                    ANCHOR_LABELS[self.quality_anchor.value]
                    if self.quality_anchor
                    else None
                ),
                "adjusted": self.adjusted_quality_score,
                "score": self.answer_quality,
            },
            "depth": {
                "anchor": (
                    self.depth_anchor.value
                    if self.depth_anchor
                    else None
                ),
                "label": (
                    ANCHOR_LABELS[self.depth_anchor.value]
                    if self.depth_anchor
                    else None
                ),
                "adjusted": self.adjusted_depth_score,
                "score": self.technical_depth,
            },
            "completeness": {
                "anchor": (
                    self.completeness_anchor.value
                    if self.completeness_anchor
                    else None
                ),
                "label": (
                    ANCHOR_LABELS[self.completeness_anchor.value]
                    if self.completeness_anchor
                    else None
                ),
                "adjusted": self.adjusted_completeness_score,
                "score": self.completeness,
            },
            "anchor_reason": self.anchor_reason,
        }
