"""从能力画像推出"下一步做什么"（纯逻辑）。

## 为什么需要它：这是"生态感"的核心

本项目目前有四个页面：资料、面试、报告、能力画像。
功能都存在，但**数据不流动**：

    资料 → 面试 → 报告 → ？（dead end）

用户面完一场，看到分数与建议，然后**产品不再告诉他做什么**。
于是下一次打开它，仍然是"从零开始点一场面试" ——
那感觉像"一个功能"，而不是"一个产品"。

这个模块补的就是"？"那一环：**把已积累的评价变成可执行的下一步**，
并且这条建议能**直接带进下一场面试**（作为检索方向）。

## 三条硬约束（都来自本项目已有结论，不是新发明的）

**① 样本少时不给"短板"结论。**

`ability_profile_service` 已经写明：少于 3 场时分数差异可能只是
评分波动（§8A.9 实测同输入重复评分极差 10–20 分）。
因此样本不足时，本模块**不给"你最弱的是 X"**，而是给
"再多面几场"或"先补齐资料"这类**不依赖统计**的建议。

**② 弱点不做语义归类。**

`ability_profile_service` 也写明：按主题聚合会**编造不存在的共性**。
因此这里只用"**重复出现的原文**"（`recurring_weaknesses`），
以及**维度中位数**这两项可靠信号。

**③ 建议必须能落成动作。**

"多练技术深度"不是建议，是口号。因此每条建议都配一个
**可执行动作**：要么是一段可直接作为面试方向的检索词，
要么是一个明确的界面动作（补资料 / 再面一场）。
"""

from dataclasses import dataclass, field

from app.services.interview.ability_profile_service import (
    MIN_INFORMATIVE_COUNT,
    AbilityProfile,
    DimensionProfile,
)

# 样本不足时给的行动建议（不依赖统计）。
ACTION_PRACTICE_MORE = "practice_more"
# 资料太少、面试只能问通用题时的行动建议。
ACTION_ADD_MATERIALS = "add_materials"
# 有足够样本 → 针对弱项再面一场。
ACTION_FOCUS_NEXT = "focus_next"
# 什么都还没有。
ACTION_START_FIRST = "start_first"


# 维度 → 可直接用于**检索**的考察方向。
#
# 为什么需要它：`query` 会被传进 `POST /interviews/{id}/start`，
# 直接作为向量检索的关键词。若只写"技术"，检索到的材料很泛；
# 而写成"实现细节、参数取值与取舍"能更准地命中资料里的对应段落。
#
# 这是"建议必须能落成动作"的具体体现 —— 光说"练技术"不是建议。
DIMENSION_FOCUS_QUERY: dict[str, str] = {
    "technical_score": "技术实现细节、参数取值与取舍理由",
    "project_score": "本人在项目中的具体职责与设计取舍",
    "communication_score": "表达是否切题、有条理、能补充细节",
}


@dataclass
class NextStep:
    """一条"下一步"建议。"""

    # 动作类型，前端据此决定显示什么按钮
    action: str

    # 给用户看的一句话（含具体数字/事实，不写空话）
    title: str

    # 为什么这么建议（引用了哪些事实）
    reason: str = ""

    # 具体要关注的点（1–3 条）。**可为空** —— 空就是"还没有判断"。 
    focus: list[str] = field(default_factory=list)

    # 可直接传给 `POST /interviews/{id}/start` 的 query。
    #
    # 为 None 表示这条建议不涉及"再面一场"（例如补资料）。
    query: str | None = None

    # 必须与建议一起展示的说明（采样事实）
    caveats: list[str] = field(default_factory=list)


def _weakest_dimension(
    profile: AbilityProfile,
) -> DimensionProfile | None:
    """分数中位数最低的**可练**维度。

    用中位数而不是均值：与 `ability_profile_service` 保持同一口径
    （那边已经论证过均值会被单次极端值拖走）。

    ⚠️ **排除「综合」**。它是四个维度的聚合分，不是一项可练习的能力。
    实测端点第一版就把它选成了"最弱维度"，于是建议文案变成
    「针对『综合』再面一场」—— 而"专注综合"对用户毫无指导意义，
    用它去检索也检不出任何具体材料。

    可练的只有三个具体维度：技术、项目、沟通。
    """

    candidates = [
        dimension
        for dimension in profile.dimensions
        if dimension.median is not None
        and dimension.key != "overall_score"
    ]

    if not candidates:
        return None

    return min(candidates, key=lambda item: item.median)


def build_next_step(
    profile: AbilityProfile,
    *,
    has_indexed_materials: bool = True,
) -> NextStep:
    """根据画像给出**一条**下一步建议。

    只给一条：给三条建议等于没有建议，用户不会挑。
    """

    # ---------------- 一场都还没面 ----------------
    if profile.session_count == 0:
        if not has_indexed_materials:
            return NextStep(
                action=ACTION_ADD_MATERIALS,
                title="先上传并索引一份资料，再开始面试",
                reason=(
                    "目前没有可被检索的资料。"
                    "没有资料时面试只能出通用题，"
                    "无法针对你的经历提问。"
                ),
            )

        return NextStep(
            action=ACTION_START_FIRST,
            title="开始第一场模拟面试",
            reason="还没有已完成的面试，因此还没有可分析的弱项。",
        )

    # ---------------- 样本不足：不下"短板"结论 ----------------
    #
    # ⚠️ 判据**从 `session_count` 直接推导**，不看
    # `profile.sufficient_samples`。
    #
    # 实测原因：那个标志由 `AbilityProfileService` 计算，
    # 而本函数也可能被别处构造的 `AbilityProfile` 调用（测试、
    # 将来的新入口）。一旦标志没被设置，就出现
    # 「再完成 **-2** 场面试」这种明显荒谬的文案 ——
    # 而它不会报错，只是把建议算错。
    #
    # 同一事实只应存在一处：**场次就是判据本身**。
    if profile.session_count < MIN_INFORMATIVE_COUNT:
        remaining = max(MIN_INFORMATIVE_COUNT - profile.session_count, 1)

        return NextStep(
            action=ACTION_PRACTICE_MORE,
            title=f"再完成 {remaining} 场面试，才能判断你的短板",
            reason=(
                f"目前只有 {profile.session_count} 场已完成的面试。"
                "同一份回答重复评分的实测极差有 10–20 分，"
                "两场之间的差距完全可能是评分波动 —— "
                "因此这里不给出「你最弱的是哪一项」。"
            ),
            caveats=list(profile.caveats),
        )

    # ---------------- 样本够：给一个可执行的聚焦方向 ----------------
    focus: list[str] = []
    reasons: list[str] = []
    # 检索用的短语（与 `focus` 里的展示文案分开）
    query_parts: list[str] = []

    weakest = _weakest_dimension(profile)

    if weakest is not None:
        focus.append(weakest.label)
        reasons.append(
            f"最近 {weakest.count} 场里「{weakest.label}」的中位数是 "
            f"{weakest.median:g} 分，是三个可练维度里最低的一项"
        )
        query_parts.append(
            DIMENSION_FOCUS_QUERY.get(
                weakest.key, weakest.label
            )
        )

    # 重复出现的弱点（原文一致）—— 这是**唯一**可靠的"具体缺口"信号。
    #
    # 只取第一条：它出现的次数最多，且多条并列时按出现次数排序后
    # 用户也很难消化更多。
    if profile.recurring_weaknesses:
        top = profile.recurring_weaknesses[0]
        focus.append(top.text)
        # 具体缺口本身就是很好的检索词（它描述的是资料里的某个技术点）
        query_parts.append(top.text)

        if top.occurrences > 1:
            reasons.append(
                f"「{top.text[:40]}」在 {top.occurrences} 场里重复出现"
            )

    if not focus:
        return NextStep(
            action=ACTION_PRACTICE_MORE,
            title="再完成一场面试，就能给出具体方向",
            reason="已有评分记录，但还没有重复出现的弱点可供定位。",
            caveats=list(profile.caveats),
        )

    # 把关注点拼成检索方向。
    #
    # 这段文字会作为 `start` 的 query 进入检索，直接影响
    # 出题的取材方向 —— 因此要写成"考察什么"，而不是"我弱什么"。
    query = "重点考察：" + "；".join(query_parts)

    return NextStep(
        action=ACTION_FOCUS_NEXT,
        title="针对这些弱项再面一场",
        reason="；".join(reasons) + "。",
        focus=focus,
        query=query,
        caveats=list(profile.caveats),
    )
