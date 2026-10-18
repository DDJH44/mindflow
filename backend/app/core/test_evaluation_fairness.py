"""验证"答不知道"的评分是否公平（§38）。

## 要验证的事故

用户实测反馈：一次面试里答"不知道"然后结束，四个维度**全是 15 分**。

排查后确认：

| 维度 | 改前 | 是否合理 |
| --- | --- | --- |
| `overall` | D (15) | ✓ "基本没有可用信息" |
| `technical` | D (15) | ✓ "未体现任何技术信息" |
| `project` | D (15) | ✓ "未涉及本人负责的部分" |
| **`communication`** | **D (15)** | ✗ **不公平** |

`communication` 的 D 档定义是"**无法有效表达**，回答与问题无关"。
而"不知道"是一个切题、清晰的回答 —— 它没有表达问题。

根因是 rubric 的【判档总则】写着"先判断是否正面回答，再判断够不够具体"，
那条规则**正确适用于内容维度**，但它被应用到了"表达"维度上，
于是"没答出内容"被推成了"表达很差"。

## 它验证什么

1. **`communication` 不再是 D**（应为 C 或更好）
2. **`overall` / `technical` / `project` 仍是 D** ——
   修复不能把"确实没有技术信息"洗成及格
3. **`anchor_reason` 提到轮次少** —— 1 题的评价不该被说成"整场结论"

用法：uv run python -m app.core.test_evaluation_fairness
"""

import asyncio
import sys

from app.core.config import settings


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


# 复现用户事故的最小输入：1 轮，回答是"不知道"。
#
# 问题取自真实会话 1444（AgentHub 项目的 WebSocket 追问）。
INCIDENT_CONTENT = """问题：你在 AgentHub 项目中提到基于 WebSocket 实现群聊消息推送，并通过 conversationId / artifactId 绑定来解决多会话产物串台问题。请具体描述一下你是如何设计数据模型的？

回答：不知道
"""

# `communication` 的可接受档位：**不能是 D**。
#
# 为什么允许 C 而不要求 B/A：一次 1 题的面试里，
# 候选人只说了两个字，**没有机会**展示条理与追问补充 ——
# 那是 B/A 档要观察的东西。因此 C 是"表达合格"的合理上界。
#
# 而 D 是明确错误的：D 的定义是"无法有效表达"。
ACCEPTABLE_COMMUNICATION = {"A", "B", "C"}


async def main() -> int:
    sys.path.insert(0, r"D:\yuxi\MindFlow\backend")

    from app.services.interview.evaluator import InterviewEvaluator

    result = Result()

    print("=" * 78)
    print("「答不知道」的评分公平性验证")
    print("=" * 78)

    print()
    print(f"  模型: {settings.llm_model}")
    print("  输入: 1 轮问答，回答为「不知道」")
    print()

    evaluator = InterviewEvaluator()

    evaluation = await evaluator.evaluate(
        interview_content=INCIDENT_CONTENT,
        repeats=1,
        # ⚠️ 必须传 —— 这正是本次修复的一部分。
        # 不传的话模型不知道只有 1 轮，会给出"整场结论"的措辞。
        turn_count=1,
    )

    print("  结果：")
    print(
        f"    综合   {evaluation.overall_score:<4} "
        f"锚点={evaluation.overall_anchor}"
    )
    print(
        f"    技术   {evaluation.technical_score:<4} "
        f"锚点={evaluation.technical_anchor}"
    )
    print(
        f"    项目   {evaluation.project_score:<4} "
        f"锚点={evaluation.project_anchor}"
    )
    print(
        f"    沟通   {evaluation.communication_score:<4} "
        f"锚点={evaluation.communication_anchor}"
    )
    print(f"    理由：{evaluation.anchor_reason}")

    # ================================================
    print()
    print("=" * 78)
    print("① 沟通维度不应被内容不足拉低")
    print("=" * 78)

    communication = (
        evaluation.communication_anchor.value
        if evaluation.communication_anchor
        else "D"
    )

    result.check(
        "communication 不是 D（「不知道」是清晰切题的）",
        communication in ACCEPTABLE_COMMUNICATION,
        f"实得 {communication}（{evaluation.communication_score} 分）",
    )

    # ================================================
    print()
    print("=" * 78)
    print("② 内容维度仍应反映「确实没有技术信息」")
    print("=" * 78)

    # 修复不能过头：把"什么都没答出来"洗成及格，
    # 那会让报告失去诊断价值。
    overall = (
        evaluation.overall_anchor.value
        if evaluation.overall_anchor
        else "D"
    )
    technical = (
        evaluation.technical_anchor.value
        if evaluation.technical_anchor
        else "D"
    )

    result.check(
        "overall 仍是 D（确实没有可用信息）",
        overall == "D",
        f"实得 {overall}（{evaluation.overall_score} 分）",
    )
    result.check(
        "technical 仍是 D（未体现任何技术信息）",
        technical == "D",
        f"实得 {technical}（{evaluation.technical_score} 分）",
    )

    # ================================================
    print()
    print("=" * 78)
    print("③ 理由要说明「本次轮次很少」")
    print("=" * 78)

    # 1 题的评价被说成"整场综合表现"会误导用户：
    # 那读起来像"你整体很差"，而实际样本量是 1。
    #
    # ⚠️ 判据必须容忍**语序与空格的变化**（D64 的同一个教训）。
    #
    # 实测：这一条第一版用的是固定关键词
    # `("1 轮", "一轮", "轮次", "样本", "题数", "仅一")`，
    # 而模型实际说的是「在**仅有的1轮**中完全未能回应…」——
    # 语义完全正确，但「1轮」之间没有空格，于是**判定失败**。
    #
    # 判据测的应当是"有没有说明样本量有限"，而不是
    # "有没有用我预想的那几个字"。
    import re

    reason = evaluation.anchor_reason or ""

    mentions_sample = bool(
        re.search(r"\d+\s*轮", reason)
        or any(
            word in reason
            for word in (
                "样本",
                "轮次",
                "仅一",
                "仅有一",
                "只有一",
                "唯一一",
                "有效作答",
            )
        )
    )

    result.check(
        "anchor_reason 说明本轮次/样本量有限",
        mentions_sample,
        f"理由：{reason[:120]}",
    )

    # ================================================
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
    sys.exit(asyncio.run(main()))
