"""能力画像：把多场面试的评价聚合成"我目前的能力状况"。

对应 MIND_FLOW_PLAN.md §9.2 的 Phase 6「能力画像与训练建议」。

**三个原则**（都来自本项目已有的实测结论）：

1. **不用分数均值当结论。** ADR-015 明确"不用 LLM 评分均值衡量模型
   升级效果"，§8A.9 实测同一输入重复 10 次的极差有 10–20 分。
   因此这里给出的是**中位数 + 区间 + 逐次明细**，并让调用方
   能看到每次的原始分数。给一个光洁的平均分会让用户以为
   它精确，而它不精确。

2. **样本少时不下结论。** 只有 1–2 场面试时，`min_informative_count`
   之下的维度不给出"优势/短板"判断，只展示已发生的事实。
   一两场的分数差异完全可能是采样噪声（§22.7）。

3. **弱点只做"原文并列"，不做自动归类。** 想按主题聚合就得做
   语义相似度或词表匹配，而两种做法都会**编造不存在的共性**：
   词表匹配会把"未说明 TTL"和"未说明重试"归成一类，
   语义匹配更糟。因此这里统计的是**重复出现的原文**
   （去空白后完全一致），其余按时间列出让用户自己看。
"""

from dataclasses import dataclass, field

from sqlalchemy.ext.asyncio import AsyncSession

from app.repositories.interview_evaluation_repository import (
    InterviewEvaluationRepository,
)

# 四个维度的键与中文名。
#
# 顺序固定：前端按此顺序展示，不依赖字典顺序。
DIMENSIONS: tuple[tuple[str, str], ...] = (
    ("overall_score", "综合"),
    ("technical_score", "技术"),
    ("project_score", "项目"),
    ("communication_score", "沟通"),
)

# 低于这个样本量时，不给出"优势 / 短板"这类判断。
#
# 为什么是 3：§8A.9 实测两场之间的分数差可能完全来自采样。
# 3 场仍然很少，但至少能看到"是否每次都低"。
# 这个阈值是**保守选择**，不是统计意义上的充分样本。
MIN_INFORMATIVE_COUNT = 3


@dataclass
class DimensionProfile:
    """单个维度的画像。"""

    key: str
    label: str

    count: int
    median: float | None
    minimum: int | None
    maximum: int | None
    latest: int | None

    # 逐次分数（**按时间正序**），供前端画趋势。
    history: list[int] = field(default_factory=list)

    @property
    def spread(self) -> int | None:
        """极差。比标准差更直观，也是本项目一直用的口径（§8A.9）。"""

        if self.minimum is None or self.maximum is None:
            return None
        return self.maximum - self.minimum


@dataclass
class RecurringWeakness:
    """重复出现的弱点（原文一致）。"""

    text: str
    occurrences: int


@dataclass
class SessionSummary:
    """一场面试的简要记录，用于"逐场明细"。"""

    session_id: int
    project_id: int
    project_name: str | None
    target_role: str | None
    evaluated_at: object
    scores: dict[str, int]


@dataclass
class AbilityProfile:
    """能力画像的完整结果。"""

    session_count: int
    dimensions: list[DimensionProfile]

    sessions: list[SessionSummary] = field(default_factory=list)

    # 最近的 weaknesses / suggestions（按时间倒序取，
    # 让用户先看到最近一次的问题）。
    recent_weaknesses: list[str] = field(default_factory=list)
    recent_suggestions: list[str] = field(default_factory=list)

    # 重复出现的弱点原文。
    recurring_weaknesses: list[RecurringWeakness] = field(
        default_factory=list
    )

    # 样本是否足以支撑"优势 / 短板"判断。
    sufficient_samples: bool = False

    # 给用户的说明。**必须暴露采样事实**，否则用户会
    # 把 10 分的差异当成真实进步/退步。
    caveats: list[str] = field(default_factory=list)


def _median(values: list[int]) -> float | None:
    """中位数。

    用中位数而不是平均值：本项目实测分数分布偶尔是双峰的，
    均值会被单次极端值拖走，而中位数更稳（§8A.9 也是这个口径）。
    """

    if not values:
        return None

    ordered = sorted(values)
    middle = len(ordered) // 2

    if len(ordered) % 2 == 1:
        return float(ordered[middle])

    return (ordered[middle - 1] + ordered[middle]) / 2


class AbilityProfileService:
    """把评价记录聚合成能力画像。"""

    def __init__(self, db: AsyncSession):
        self.db = db
        self.evaluation_repository = InterviewEvaluationRepository(db)

    async def build(
        self,
        user_id: int,
        project_id: int | None = None,
        project_names: dict[int, str] | None = None,
        limit: int = 50,
    ) -> AbilityProfile:
        # `structured_only` 默认开启：只统计带结构化内容
        # （strengths / weaknesses / suggestions 至少一组非空）的评价。
        #
        # 三组全空的记录来自 D20 落库之前 —— 只有四项分数与
        # `feedback`。把它们算进趋势会**用占位数据撑大区间**，
        # 而用户无从分辨（实测把 mindflow 的画像撑成
        # "共 6 次测量、区间 30–90"，其中一半是占位数据）。
        rows = await self.evaluation_repository.get_by_user(
            user_id=user_id,
            project_id=project_id,
            limit=limit,
        )

        names = project_names or {}

        # ---------------- 逐维度聚合 ----------------
        dimensions: list[DimensionProfile] = []

        for key, label in DIMENSIONS:
            history = [
                int(getattr(evaluation, key) or 0)
                for evaluation, _session in rows
            ]

            dimensions.append(
                DimensionProfile(
                    key=key,
                    label=label,
                    count=len(history),
                    median=_median(history),
                    minimum=min(history) if history else None,
                    maximum=max(history) if history else None,
                    latest=history[-1] if history else None,
                    history=history,
                )
            )

        # ---------------- 逐场明细 ----------------
        sessions = [
            SessionSummary(
                session_id=session.id,
                project_id=session.project_id,
                project_name=names.get(session.project_id),
                target_role=session.target_role,
                evaluated_at=evaluation.created_at,
                scores={
                    key: int(getattr(evaluation, key) or 0)
                    for key, _label in DIMENSIONS
                },
            )
            for evaluation, session in rows
        ]

        # ---------------- 弱点与建议 ----------------
        # 按时间**倒序**取，让用户先看到最近一次的问题。
        recent_weaknesses: list[str] = []
        recent_suggestions: list[str] = []
        weakness_counter: dict[str, int] = {}

        for evaluation, _session in reversed(rows):
            for text in evaluation.weaknesses or []:
                cleaned = " ".join(str(text).split())
                if not cleaned:
                    continue

                weakness_counter[cleaned] = (
                    weakness_counter.get(cleaned, 0) + 1
                )

                if cleaned not in recent_weaknesses:
                    recent_weaknesses.append(cleaned)

            for text in evaluation.suggestions or []:
                cleaned = " ".join(str(text).split())
                if cleaned and cleaned not in recent_suggestions:
                    recent_suggestions.append(cleaned)

        recurring = [
            RecurringWeakness(text=text, occurrences=count)
            for text, count in weakness_counter.items()
            if count > 1
        ]
        recurring.sort(key=lambda item: item.occurrences, reverse=True)

        # ---------------- 说明 ----------------
        caveats = []

        if len(rows) < MIN_INFORMATIVE_COUNT:
            caveats.append(
                f"目前只有 {len(rows)} 场已完成的面试。"
                f"少于 {MIN_INFORMATIVE_COUNT} 场时，"
                "分数之间的差异可能只是评分波动，"
                "因此这里不给出「优势 / 短板」判断。"
            )

        spreads = [
            dimension.spread
            for dimension in dimensions
            if dimension.spread is not None
        ]

        if spreads and max(spreads) >= 10:
            caveats.append(
                "各维度极差最大达到 "
                f"{max(spreads)} 分。同一份回答重复评分的实测极差"
                "本身就有 10–20 分（§8A.9），因此"
                "不要把 10 分左右的差异当作真实进步或退步；"
                "看趋势要结合多次面试。"
            )

        sufficiency = (
            "suggestions / weaknesses 来自模型对每场面试的文字评价，"
            "不是标准化量表；它们用于提示方向，不是诊断结论。"
        )
        caveats.append(sufficiency)

        return AbilityProfile(
            session_count=len(rows),
            dimensions=dimensions,
            sessions=sessions,
            recent_weaknesses=recent_weaknesses[:10],
            recent_suggestions=recent_suggestions[:10],
            recurring_weaknesses=recurring[:5],
            sufficient_samples=len(rows) >= MIN_INFORMATIVE_COUNT,
            caveats=caveats,
        )
