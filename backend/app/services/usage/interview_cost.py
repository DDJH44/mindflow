"""面试成本预估（纯逻辑，不访问数据库、不调 LLM）。

## 它解决什么问题

实测现状：用户只能看到"本月剩余 9 场、98 题"，
却**不知道该设几题**。而额度是两份独立的约束：

- 场次：开一场扣 1 场
- 题目：出一题扣 1 题（**追问也计入**）

于是存在一个用户看不见的陷阱：

> 剩余 2 场、8 题时，用户把题目上限设成 8 并开始面试 ——
> **第 3 场能开，但第 9 题会被拒**，面试在中间卡住。
> 而那时额度已经花掉了，用户既没得到完整面试，也拿不回场次。

也就是说，**"能不能开始"与"能不能开完"是两个不同的问题**，
而当时的界面只回答了前者。

## 判据：消耗上界是确定的

`decide_continue` 用 `questions_asked < max_questions` 判定继续，
而 `questions_asked` **含追问**。因此：

    一场面试最多消耗 max_questions 道题额度

这是**上界**，不是估算 —— 不需要历史数据、不需要统计模型。
因此本模块可以给出确定结论：

| 结论 | 含义 |
| --- | --- |
| `fits` | 场次够、题数也够，能完整开完 |
| `insufficient_questions` | 场次够，但题目额度不够开完 |
| `insufficient_interviews` | 场次不够，开不了 |

`insufficient_questions` 是**最需要提前告知**的那种 ——
它不会阻止用户开始，只会让面试在中途断掉。
"""

from dataclasses import dataclass

from app.services.interview.interview_termination import (
    normalize_max_questions,
)


@dataclass(frozen=True)
class CostEstimate:
    """一场面试的成本预估。"""

    # 规范化之后的题目预算（用户可能设了越界值）
    questions: int

    # 这一场需要的场次额度（恒为 1）
    interviews: int

    # 是否够开完
    fits: bool

    # 不够时的原因：`insufficient_questions` / `insufficient_interviews`
    blocked_by: str | None

    # 是否因为**题目额度**不足而会在中途断掉
    #
    # 这个字段单独给出，因为它是唯一"能开始但开不完"的情况 ——
    # 而那种情况最需要提前警告：用户已经付出了一场。
    truncates_at: int | None

    def describe(self, questions_remaining: int) -> str:
        """给用户看的一句话说明。

        ⚠️ 文案规则来自 `docs/UI_DESIGN.md` §9.2：
        能写具体数字就写，不用感叹号，不用"您"。
        """

        if self.blocked_by == "insufficient_interviews":
            return "本月面试场次已用完，无法开始新的面试。"

        if self.blocked_by == "insufficient_questions":
            return (
                f"本月剩余 {questions_remaining} 题，"
                f"而这一场设了 {self.questions} 题 —— "
                f"第 {questions_remaining + 1} 题会被拒，面试会中途中断。"
            )

        return (
            f"这一场最多 {self.questions} 题，"
            f"本月剩余额度足够开完。"
        )


def estimate_cost(
    *,
    max_questions: int | None,
    interviews_remaining: int,
    questions_remaining: int,
) -> CostEstimate:
    """预估一场面试是否开得完。

    参数都是`当前剩余`，因此调用方需先读用量。
    """

    questions = normalize_max_questions(max_questions)

    # 场次不够：直接开不了。
    # 先判它，因为"开不了"比"开不完"更早发生 ——
    # 一个开不了的面试，题目额度够不够没有意义。
    if interviews_remaining < 1:
        return CostEstimate(
            questions=questions,
            interviews=1,
            fits=False,
            blocked_by="insufficient_interviews",
            truncates_at=None,
        )

    # 题目额度不够开完。
    #
    # 注意这里不判定"能不能至少出一题"：
    # 出不了第一题时，`start_interview` 会自己返回 429 并退还场次
    # （D61 的 `refund()`）—— 也就是说那条路径**不会让用户损失**。
    # 而"开到一半断掉"会损失，因此那才是本模块要拦的。
    if questions_remaining < questions:
        return CostEstimate(
            questions=questions,
            interviews=1,
            fits=False,
            blocked_by="insufficient_questions",
            truncates_at=questions_remaining,
        )

    return CostEstimate(
        questions=questions,
        interviews=1,
        fits=True,
        blocked_by=None,
        truncates_at=None,
    )
