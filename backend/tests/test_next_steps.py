"""验证「下一步做什么」的决策逻辑。

## 它防的是什么

这张卡片是首页最靠上的东西。**它一旦算错，用户就会照着一个
错误的方向再面一场** —— 而那种错误不会被异常暴露出来，
只会让建议显得莫名其妙。

实测踩过的两个：

1. **「再完成 -2 场面试」**。判据原本读 `profile.sufficient_samples`
   这个标志，而它和 `session_count` 可能不一致（标志没被设置时），
   于是算出一个负数。现在改为**直接从场次推导**。
2. **「针对『综合』再面一场」**。`overall_score` 是四个维度的
   聚合分，不是可练的能力。把它当成"最弱维度"，
   建议就没有任何指导意义，用它去检索也检不出具体材料。

## 它验证

- 场次不足时**不**给"短板"结论（尊重 §8A.9 的采样结论）
- 场次足够时选出**可练**维度（排除"综合"）
- 有重复弱项时，弱项原文进入 `focus` 与 `query`
- `query` 是可检索的短语，不是"技术"这种泛词
- 没有资料时有专门的建议

用法：uv run python -m tests.test_next_steps
"""

import sys

from app.services.interview.ability_profile_service import (
    AbilityProfile,
    DimensionProfile,
    RecurringWeakness,
)
from app.services.interview.next_steps import (
    ACTION_ADD_MATERIALS,
    ACTION_FOCUS_NEXT,
    ACTION_PRACTICE_MORE,
    ACTION_START_FIRST,
    build_focus_from_report,
    build_next_step,
)


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


def dimension(
    key: str,
    label: str,
    median: float,
    count: int = 5,
) -> DimensionProfile:
    return DimensionProfile(
        key=key,
        label=label,
        count=count,
        median=median,
        minimum=int(median) - 10,
        maximum=int(median) + 10,
        latest=int(median),
        history=[],
    )


def main() -> int:
    sys.path.insert(0, r"D:\yuxi\MindFlow\backend")

    result = Result()

    print("=" * 78)
    print("下一步建议验证")
    print("=" * 78)

    # ================================================
    print()
    print("=" * 78)
    print("① 没有面试 / 没有资料")
    print("=" * 78)

    step = build_next_step(
        AbilityProfile(session_count=0, dimensions=[]),
        has_indexed_materials=True,
    )
    result.check(
        "没有面试 → 开始第一场",
        step.action == ACTION_START_FIRST,
        step.action,
    )

    step = build_next_step(
        AbilityProfile(session_count=0, dimensions=[]),
        has_indexed_materials=False,
    )
    result.check(
        "没有资料 → 先补资料",
        step.action == ACTION_ADD_MATERIALS,
        step.action,
    )

    # ================================================
    print()
    print("=" * 78)
    print("② 样本不足：不给短板结论（§8A.9）")
    print("=" * 78)

    # 这一条是**原则性**的：两场之间的分数差可能完全来自评分波动，
    # 此时说"你最弱的是技术"会让用户去练一个实际上没问题的地方。
    short = AbilityProfile(
        session_count=2,
        dimensions=[
            dimension("technical_score", "技术", 40, count=2),
            dimension("project_score", "项目", 80, count=2),
        ],
    )

    step = build_next_step(short)

    result.check(
        "2 场 → 不给短板，建议再面几场",
        step.action == ACTION_PRACTICE_MORE,
        step.action,
    )
    result.check(
        "建议里不出现「你最弱的是」这类结论",
        "最弱" not in step.title,
        step.title,
    )
    result.check(
        "剩余场次是正数（曾算出 -2）",
        "再完成 1 场" in step.title or "再完成 2 场" in step.title,
        step.title,
    )

    # ================================================
    print()
    print("=" * 78)
    print("③ 样本足够：选出可练维度（排除「综合」）")
    print("=" * 78)

    # 「综合」刻意给最低分：它**不该**被选为聚焦方向。
    enough = AbilityProfile(
        session_count=5,
        dimensions=[
            dimension("overall_score", "综合", 15),
            dimension("technical_score", "技术", 40),
            dimension("project_score", "项目", 70),
            dimension("communication_score", "沟通", 80),
        ],
    )

    step = build_next_step(enough)

    result.check(
        "给出「再面一场」建议",
        step.action == ACTION_FOCUS_NEXT,
        step.action,
    )
    result.check(
        "「综合」不被当作聚焦方向（它是聚合分）",
        "综合" not in step.focus,
        f"focus={step.focus}",
    )
    result.check(
        "聚焦到真正最低的可练维度",
        "技术" in step.focus,
        f"focus={step.focus}",
    )
    result.check(
        "query 是可检索的短语，不是「技术」两个字",
        step.query is not None and "实现细节" in step.query,
        f"{step.query}",
    )

    # ================================================
    print()
    print("=" * 78)
    print("④ 重复弱项进入 focus 与 query")
    print("=" * 78)

    with_gap = AbilityProfile(
        session_count=5,
        dimensions=[
            dimension("technical_score", "技术", 40),
            dimension("project_score", "项目", 70),
        ],
        recurring_weaknesses=[
            RecurringWeakness(
                text="未说明 chunk_id 与 metadata 的映射",
                occurrences=3,
            )
        ],
    )

    step = build_next_step(with_gap)

    result.check(
        "重复弱项原文进入 focus",
        any("chunk_id" in item for item in step.focus),
        f"focus={step.focus}",
    )
    result.check(
        "重复弱项原文进入 query（于是下一场会问到这个方向）",
        step.query is not None and "chunk_id" in step.query,
        f"{step.query}",
    )
    result.check(
        "理由里说明了它出现过几次",
        "3 场" in step.reason,
        step.reason[:80],
    )

    # ================================================
    print()
    print("=" * 78)
    print("⑤ 只有一条建议（给三条等于没有）")
    print("=" * 78)

    # 结构上 `focus` 最多两条：一个维度 + 一条重复弱项。
    # 再多用户不会挑。
    result.check(
        "focus 最多 2 条",
        len(step.focus) <= 2,
        f"{len(step.focus)} 条",
    )

    # ================================================
    print()
    print("=" * 78)
    print("⑥ 报告页聚焦：单场回顾（build_focus_from_report）")
    print("=" * 78)

    # 与 build_next_step 的边界：单场内容回顾**不受**
    # "少于 3 场不下结论"约束 —— 那条约束的是跨场能力归因。
    # 报告页本来就展示了这一场的弱项，"下场重点考察它们"
    # 一场也成立。
    report_focus = build_focus_from_report(
        overall_score=15,
        technical_score=35,
        project_score=60,
        communication_score=75,
        weaknesses=[
            "未说明 chunk_id 与 metadata 的映射",
            "第二条弱项不应该进入聚焦（只取第一条）",
        ],
    )

    result.check(
        "单场也给出聚焦方向（无需 3 场样本）",
        report_focus is not None,
        "None" if report_focus is None else "有",
    )

    if report_focus is not None:
        result.check(
            "「综合」最低但不被选（聚合分不可练）",
            "综合" not in report_focus.focus
            and "聚合" not in report_focus.query,
            f"focus={report_focus.focus}",
        )
        result.check(
            "选本场最低的可练维度（技术）",
            "技术" in report_focus.focus,
            f"focus={report_focus.focus}",
        )
        result.check(
            "维度进入 query 时用可检索短语，不是「技术」两个字",
            "实现细节" in report_focus.query,
            report_focus.query,
        )
        result.check(
            "首条弱项原文进入 focus 与 query",
            any("chunk_id" in item for item in report_focus.focus)
            and "chunk_id" in report_focus.query,
            f"focus={report_focus.focus}",
        )
        result.check(
            "第二条弱项不进入（只取第一条，多条用户消化不了）",
            "第二条" not in report_focus.query,
            report_focus.query,
        )
        result.check(
            "focus 最多 2 条（一个维度 + 一条弱项）",
            len(report_focus.focus) <= 2,
            f"{len(report_focus.focus)} 条",
        )

    # 维度分数是评价的必填项 → 函数**总有**维度方向、不返回 None。
    # 这里验证弱项为空时：不崩溃、focus 退化为只剩维度一条。
    no_weakness = build_focus_from_report(
        overall_score=50,
        technical_score=50,
        project_score=50,
        communication_score=50,
        weaknesses=[],
    )

    result.check(
        "弱项为空时仍有维度方向（不崩溃、不返回 None）",
        no_weakness is not None
        and len(no_weakness.focus) == 1,
        "None"
        if no_weakness is None
        else f"focus={no_weakness.focus}",
    )

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
    sys.exit(main())
