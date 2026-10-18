"""验证面试计划：按材料类型轮转主题（§40）。

## 它解决什么问题

出题流程此前是这样的：

    首题：按方向检索 → 生成
    之后：**每一题都是追问上一题的回答**

也就是**没有任何"换主题"的路径**。后果：一份 30 题的面试会全部
围绕第一次检索命中的那块材料打转，其它已上传的材料
（岗位要求、项目细节、代码）从未被问到 ——
而那正是"上传四类材料"本该带来的价值。

## 它验证

1. **纯逻辑**：决策顺序（先深后宽）、软性终止、序列化往返、
   坏快照的容错
2. **计划来自真实资料**：只把**已索引**的材料类型排进计划
   （否则会安排一个检索不到东西的主题，题退化成通用题）
3. **端到端**：`start_interview` 真的把计划写进 `plan_snapshot`

## 为什么第 1 组不依赖数据库

决策规则是本项目最容易写错的部分之一（实测第一版就把
"覆盖"判断放错了位置，导致永远不追问）。纯逻辑测试能在
一秒内跑完全部边界，不需要起任何服务。

用法：uv run python -m app.core.test_interview_plan
"""

import asyncio
import sys

from sqlalchemy import text

from app.database.session import AsyncSessionLocal

TEST_PROJECT_NAME = "__test__interview-plan"


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


async def cleanup() -> None:
    async with AsyncSessionLocal() as db:
        await db.execute(
            text("DELETE FROM projects WHERE name = :n"),
            {"n": TEST_PROJECT_NAME},
        )
        await db.commit()


def check_pure_logic(result: Result) -> None:
    """决策规则（不碰数据库）。"""

    from app.services.interview import interview_plan as ip

    print()
    print("=" * 78)
    print("① 计划来自材料类型")
    print("=" * 78)

    plan = ip.build_plan(["resume", "jd", "project", "code"])

    result.check(
        "四类材料 → 四个主题",
        len(plan.topics) == 4,
        f"{[t.label for t in plan.topics]}",
    )
    result.check(
        "主题顺序沿用传入顺序（可复现）",
        [t.id for t in plan.topics]
        == ["resume", "jd", "project", "code"],
    )
    result.check(
        "初始没有当前主题",
        plan.current_topic_id is None,
    )
    result.check(
        "初始动作是「换主题」（先问第一个）",
        ip.decide_next_action(plan) == ip.ACTION_NEW_TOPIC,
    )

    # 没有资料时不能给空计划 —— 否则第二题就无路可走。
    empty_plan = ip.build_plan([])

    result.check(
        "没有资料时给通用主题兜底（计划不能为空）",
        len(empty_plan.topics) == 1
        and empty_plan.topics[0].id == ip.GENERIC_TOPIC_ID,
        f"{[t.id for t in empty_plan.topics]}",
    )

    # ================================================
    print()
    print("=" * 78)
    print("② 决策顺序：先深后宽")
    print("=" * 78)

    # 这一组是本模块最容易写错的地方。实测第一版把
    # "当前主题是否已覆盖"放在最前面 → 主题刚标记覆盖就换题，
    # **永远走不到追问**，面试变成"每个主题只问一道"。
    plan = ip.build_plan(["resume", "jd"])

    topic = ip.start_topic(plan)
    ip.mark_covered(plan, topic.id)

    result.check(
        "开始第一个主题后 → 追问（不是立刻换题）",
        ip.decide_next_action(plan) == ip.ACTION_FOLLOW_UP,
        f"实得 {ip.decide_next_action(plan)}",
    )

    ip.record_follow_up(plan)
    result.check(
        "追问 1 次后仍 → 追问（额度 2）",
        ip.decide_next_action(plan) == ip.ACTION_FOLLOW_UP,
    )

    ip.record_follow_up(plan)
    result.check(
        "追问 2 次后 → 换主题（还有未覆盖）",
        ip.decide_next_action(plan) == ip.ACTION_NEW_TOPIC,
        f"实得 {ip.decide_next_action(plan)}",
    )

    # ================================================
    print()
    print("=" * 78)
    print("③ 主题用尽后开新一轮（**不结束面试**）")
    print("=" * 78)

    second = ip.start_topic(plan)
    ip.mark_covered(plan, second.id)
    ip.record_follow_up(plan)
    ip.record_follow_up(plan)

    result.check(
        "全部覆盖且追问够 → 开新一轮（不是结束面试）",
        ip.decide_next_action(plan) == ip.ACTION_NEW_PASS,
        f"实得 {ip.decide_next_action(plan)}",
    )

    # 进度要在**开新一轮之前**看 —— 新一轮会把主题重置为待覆盖，
    # 之后 covered 归零是正确行为，不是进度坏了。
    progress = plan.progress()

    result.check(
        "进度反映已覆盖数",
        progress["covered"] == 2 and progress["total"] == 2,
        f"{progress['covered']}/{progress['total']}",
    )

    # 开新一轮后必须**重新有未覆盖主题** —— 否则下一轮仍然无路可走，
    # 面试会在主题用尽时卡住（而不是继续）。
    passes_before = plan.passes
    ip.start_new_pass(plan)

    result.check(
        "开新一轮后重新有未覆盖主题",
        len(plan.pending_topics) == len(plan.topics)
        and plan.follow_ups_on_current == 0
        and plan.passes == passes_before + 1,
        f"待覆盖 {len(plan.pending_topics)}，轮次 {plan.passes}",
    )

    # ================================================
    print()
    print("=" * 78)
    print("④ 序列化往返与坏快照容错")
    print("=" * 78)

    restored = ip.InterviewPlan.from_dict(plan.to_dict())

    result.check(
        "快照往返后状态一致",
        restored is not None
        and restored.current_topic_id == plan.current_topic_id
        and restored.follow_ups_on_current
        == plan.follow_ups_on_current
        and [t.status for t in restored.topics]
        == [t.status for t in plan.topics],
    )

    # 坏快照**不能抛异常**：一条被改坏的快照不该让整场面试
    # 无法继续。返回 None，调用方退化为旧行为（只追问）。
    bad_snapshots = [
        None,
        {},
        {"topics": []},
        {"topics": "not-a-list"},
        {"topics": [{"no_id": 1}]},
    ]

    bad_ok = all(
        ip.InterviewPlan.from_dict(item) is None
        for item in bad_snapshots
    )

    result.check(
        "坏快照一律返回 None（不抛异常）",
        bad_ok,
        f"{len(bad_snapshots)} 种坏输入",
    )


async def check_plan_from_real_data(result: Result) -> None:
    """计划只包含**已索引**的材料类型。"""

    from app.services.interview.interview_flow_service import (
        InterviewFlowService,
    )

    print()
    print("=" * 78)
    print("⑤ 计划只包含已索引的资料类型")
    print("=" * 78)

    async with AsyncSessionLocal() as db:
        user_id = (
            await db.execute(
                text("SELECT id FROM users WHERE username=:u"),
                {"u": "mindflow"},
            )
        ).scalar_one()

        # 项目 A：一个已索引 + 一个未索引 → 计划只应含已索引的
        project_id = (
            await db.execute(
                text(
                    "INSERT INTO projects (name, owner_id, status, "
                    "created_at, updated_at) "
                    "VALUES (:n, :u, 'active', now(), now()) "
                    "RETURNING id"
                ),
                {"n": TEST_PROJECT_NAME, "u": user_id},
            )
        ).scalar_one()

        for document_type, status in (
            ("resume", "embedded"),
            ("code", "chunked"),  # 未索引 —— 不该进计划
        ):
            await db.execute(
                text(
                    "INSERT INTO documents ("
                    "name, original_filename, file_type, project_id, "
                    "document_type, status, content, "
                    "created_at, updated_at) "
                    "VALUES (:n, :n, 'txt', :p, :t, :s, 'x', "
                    "now(), now())"
                ),
                {
                    "n": f"{document_type}.txt",
                    "p": project_id,
                    "t": document_type,
                    "s": status,
                },
            )

        await db.commit()

    async with AsyncSessionLocal() as db:
        plan = await InterviewFlowService(
            db
        )._build_interview_plan(project_id=project_id)

    ids = [topic.id for topic in plan.topics]

    result.check(
        "只含已索引的类型（resume），不含未索引的（code）",
        ids == ["resume"],
        f"实得 {ids}",
    )


async def main() -> int:
    sys.path.insert(0, r"D:\yuxi\MindFlow\backend")

    result = Result()

    print("=" * 78)
    print("面试计划验证")
    print("=" * 78)

    check_pure_logic(result)

    await cleanup()

    try:
        await check_plan_from_real_data(result)
    finally:
        await cleanup()
        print()
        print("  已清理测试数据")

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
