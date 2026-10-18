"""面试计划：主题覆盖与下一步决策（**纯逻辑**）。

## 它解决什么问题（实测发现）

出题流程此前是这样的：

    首题：按方向检索 → 生成
    之后：**每一题都是追问上一题的回答**

也就是说**没有任何"换主题"的路径**。后果很具体：

> 一份 30 题的面试会全部围绕**第一次检索命中的那一块材料**打转。
> 后面的 29 题都是对同一段内容的层层追问 ——
> 而候选人的其它材料（岗位要求、项目细节、代码）从未被问到。

那样的"面试"对有真实经历的候选人是不公平的：他可能在一个点上
记得不牢，而整场面试都在那里挖。同时它也浪费了已上传的资料 ——
上传四类材料的作用，本应是被分别考察。

## 做法：按**材料类型**轮转主题

不引入"LLM 生成一份话题清单"的做法，理由：

- **多一次 LLM 调用**：开始面试已经是 15–60 秒，再加一次
  只会让人以为卡住了
- **话题清单不可验证**：模型编出来的"话题"可能根本不在这批材料里，
  而本项目的底线是"提问必须能指回资料"（§9.3）

因此主题就是**材料类型**（resume / jd / project / code）——
它天然来自资料、天然可验证（每道题都带 `evidence_chunk_ids`）、
且覆盖它等于**把上传的四类材料各考察一遍**，这正是上传四类材料的意义。

`plan_snapshot` 记录的就是这个轮转计划与进度。

## 决策规则（刻意保持简单）

每一轮作答之后决定下一步做什么：

| 条件 | 动作 |
| --- | --- |
| 当前主题的追问次数 < `max_follow_ups_per_topic` | **follow_up**（先问深） |
| 还有未覆盖主题 | **new_topic**（再问宽） |
| 都覆盖了且追问也够了 | **finish**（软性终止） |

"先深后宽"的顺序是有意的：一个主题问到第二层追问才能看出
候选人是真懂还是背过；只问一层就换题，等于每处都只碰到表面。

## 与硬预算的关系

`max_questions`（单场题量上限）是**硬预算**，仍由
`decide_continue` 负责。本模块只回答"下一步问什么"，
不负责"还能不能问" —— 两件事分开，避免规则互相打架。
"""

from dataclasses import dataclass, field

# 计划快照的版本号。
#
# 存进 `plan_snapshot` 后会被长期保留，而计划结构将来一定会变。
# 版本号让"旧快照怎么读"有据可依，而不是靠猜字段。
PLAN_VERSION = 1

# 每个主题最多追问几次。
#
# 取 2：一次追问能问出"有没有真的做过"，两次能问出"做的时候
# 遇到什么、怎么取舍"。三次以上就变成了在同一个点上纠缠，
# 而那时其它主题还没被问到。
DEFAULT_MAX_FOLLOW_UPS = 2

# 材料类型的中文名（给用户看）。
DOCUMENT_TYPE_LABELS = {
    "resume": "简历经历",
    "jd": "岗位要求",
    "project": "项目细节",
    "code": "代码实现",
}

# 没有任何资料时的兜底主题。
#
# 为什么需要：资料为空时面试仍然要能进行（只是退化为通用题）。
# 若计划为空，"下一步"就没有可选项，面试会在第二题卡住。
GENERIC_TOPIC_ID = "generic"


@dataclass
class Topic:
    """计划里的一个主题。"""

    id: str
    label: str
    status: str = "pending"  # pending | covered

    @property
    def covered(self) -> bool:
        return self.status == "covered"

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "label": self.label,
            "status": self.status,
        }

    @classmethod
    def from_dict(cls, data: dict) -> "Topic":
        return cls(
            id=data["id"],
            label=data.get("label", data["id"]),
            status=data.get("status", "pending"),
        )


@dataclass
class InterviewPlan:
    """面试计划（会被序列化进 `plan_snapshot`）。"""

    topics: list[Topic]
    current_topic_id: str | None = None
    follow_ups_on_current: int = 0
    max_follow_ups_per_topic: int = DEFAULT_MAX_FOLLOW_UPS
    main_questions: int = 0
    version: int = PLAN_VERSION

    # 已经走到第几轮（主题被重新打开的次数 + 1）。
    # 复盘时能看出"这场面试把同一批材料走了两轮"。
    passes: int = 1

    # -------------------------------------------------- 序列化

    def to_dict(self) -> dict:
        return {
            "version": self.version,
            "strategy": "document_type_rotation",
            "topics": [topic.to_dict() for topic in self.topics],
            "current_topic_id": self.current_topic_id,
            "follow_ups_on_current": self.follow_ups_on_current,
            "max_follow_ups_per_topic": (
                self.max_follow_ups_per_topic
            ),
            "main_questions": self.main_questions,
            "passes": self.passes,
        }

    @classmethod
    def from_dict(cls, data: dict | None) -> "InterviewPlan | None":
        """从快照还原。返回 None 表示快照不可用。

        **不可用时返回 None 而不是抛异常**：`plan_snapshot` 可能
        是旧版本写的、也可能被手工改坏。一条坏快照不该让整场
        面试无法继续 —— 调用方会退化为"没有计划"的旧行为。
        """

        if not data or not isinstance(data, dict):
            return None

        raw_topics = data.get("topics")

        if not isinstance(raw_topics, list) or not raw_topics:
            return None

        try:
            topics = [Topic.from_dict(item) for item in raw_topics]
        except (KeyError, TypeError):
            return None

        return cls(
            topics=topics,
            current_topic_id=data.get("current_topic_id"),
            follow_ups_on_current=int(
                data.get("follow_ups_on_current") or 0
            ),
            max_follow_ups_per_topic=int(
                data.get("max_follow_ups_per_topic")
                or DEFAULT_MAX_FOLLOW_UPS
            ),
            main_questions=int(data.get("main_questions") or 0),
            version=int(data.get("version") or PLAN_VERSION),
            passes=int(data.get("passes") or 1),
        )

    # -------------------------------------------------- 查询

    @property
    def current_topic(self) -> Topic | None:
        for topic in self.topics:
            if topic.id == self.current_topic_id:
                return topic
        return None

    @property
    def pending_topics(self) -> list[Topic]:
        return [topic for topic in self.topics if not topic.covered]

    @property
    def all_covered(self) -> bool:
        return not self.pending_topics

    def progress(self) -> dict:
        """给界面用的进度。"""

        covered = len([t for t in self.topics if t.covered])

        return {
            "total": len(self.topics),
            "covered": covered,
            "passes": self.passes,
            "topics": [
                {
                    "id": topic.id,
                    "label": topic.label,
                    "covered": topic.covered,
                }
                for topic in self.topics
            ],
        }


def build_plan(
    document_types: list[str],
    *,
    max_follow_ups_per_topic: int = DEFAULT_MAX_FOLLOW_UPS,
) -> InterviewPlan:
    """按可用材料类型建立计划。

    `document_types` 应当只包含**该项目下真的有资料**的类型 ——
    否则会给候选人一个"没有内容的主题"，出题时检索不到东西，
    题就退化成通用题，而那看起来像检索坏了。

    顺序沿用调用方给的顺序（`InterviewContextBuilder.DOCUMENT_TYPES`
    的固定次序），让计划顺序稳定、可复现。
    """

    topics: list[Topic] = []

    for document_type in document_types:
        topics.append(
            Topic(
                id=document_type,
                label=DOCUMENT_TYPE_LABELS.get(
                    document_type, document_type
                ),
            )
        )

    if not topics:
        # 没有资料：仍然要有计划，否则第二题就无路可走。
        topics = [
            Topic(id=GENERIC_TOPIC_ID, label="通用技术能力"),
        ]

    return InterviewPlan(
        topics=topics,
        max_follow_ups_per_topic=max_follow_ups_per_topic,
    )


# 下一步动作
ACTION_FOLLOW_UP = "follow_up"
ACTION_NEW_TOPIC = "new_topic"
ACTION_NEW_PASS = "new_pass"


def decide_next_action(plan: InterviewPlan) -> str:
    """决定下一步：追问 / 换主题 / 开新一轮。

    ## 为什么"先深后宽"

    一个主题问到第二层追问，才能看出候选人是真懂还是背过。
    只问一层就换题，等于每处都只碰到表面 —— 那对双方都没价值。

    ## ⚠️ 为什么**不做**"主题覆盖完就结束"（软性终止）

    这是本项目一条被否掉的方案，理由值得记下来。

    最初的设计是：所有主题覆盖完、追问也够了 → 结束。
    但实测暴露了两个问题：

    **① 它会把用户设的题量变成谎言。**
    用户把 `max_questions` 设成 8，是想要 8 道题。而主题数取决于
    "这个项目上传了几类材料" —— 实测项目里常见的情况是
    只有 `resume` 一类（`other` 不在四类材料里）。
    于是一个主题 × (1 + 2 次追问) = **3 题就"自然结束"**。
    用户看到的是"我设了 8 题，第 3 题就说面试结束了"。

    **② 主题数不是用户能控制的东西。**
    用"材料的种类数"决定面试长度，等于让上传文件的偶然情况
    决定面试多长 —— 而这是用户完全无从预期的。

    因此：**题量只由硬预算（`max_questions`）决定。**
    主题只用来决定"这一题问什么方向"，不用来决定"还问不问"。

    主题用尽后**开新一轮**（把它们重新标记为待覆盖），
    于是面试既能保持"深浅交替"的节奏，又不会提前结束。

    ## 判断顺序不能颠倒（实测踩过）

    第一版把"当前主题是否已覆盖"放在最前面，于是：

        start_topic() → mark_covered() → follow_ups_on_current = 0

    主题刚被标记覆盖，决策立刻返回 `new_topic` —— **永远走不到追问**。

    原因是对"覆盖"的定义：**覆盖 = 我们为它问过主题题**，
    不是"我们问完了"。追问发生在一个已覆盖主题上，是正常且必要的。

    因此顺序必须是：**先看追问额度，再看还有没有新主题。**
    """

    # 还没开始：先问第一个主题。
    if plan.current_topic_id is None:
        return ACTION_NEW_TOPIC

    # 先问深：当前主题还有追问额度就继续追问。
    #
    # 这一步刻意**不看**当前主题是否已覆盖 —— 见上面那段说明。
    if plan.follow_ups_on_current < plan.max_follow_ups_per_topic:
        return ACTION_FOLLOW_UP

    # 再问宽：还有未覆盖主题就换。
    if plan.pending_topics:
        return ACTION_NEW_TOPIC

    # 都问过了：**开新一轮**（不结束）。
    return ACTION_NEW_PASS


def start_topic(
    plan: InterviewPlan,
    topic_id: str | None = None,
) -> Topic:
    """开始一个新主题：标记当前、清零追问计数。

    `topic_id` 省略时取第一个未覆盖主题。
    没有可开始的主题时抛 `ValueError` —— 调用方应当先用
    `decide_next_action` 判断，不该走到这里。
    """

    if topic_id is None:
        pending = plan.pending_topics

        if not pending:
            raise ValueError("没有待覆盖的主题")

        topic = pending[0]
    else:
        topic = next(
            (item for item in plan.topics if item.id == topic_id),
            None,
        )

        if topic is None:
            raise ValueError(f"计划里没有主题 {topic_id!r}")

    plan.current_topic_id = topic.id
    plan.follow_ups_on_current = 0
    plan.main_questions += 1

    return topic


def mark_covered(plan: InterviewPlan, topic_id: str) -> None:
    """把主题标记为已覆盖。

    何时算"覆盖"：**为它问出过一道主题题**（不是"候选人答得好"）。
    这个问题口径很重要：覆盖是"我们问过了"，不是"他答上来了"。
    若按回答质量决定是否覆盖，答得差的人会被反复问同一主题 ——
    而那会让面试变成针对弱点的拷问，不是评估。
    """

    for topic in plan.topics:
        if topic.id == topic_id:
            topic.status = "covered"
            return


def start_new_pass(plan: InterviewPlan) -> int:
    """开始新一轮：把所有主题重新标记为待覆盖。

    返回这是第几轮（从 1 开始计）。

    ## 为什么需要"新一轮"

    主题数取决于"这个项目上传了几类材料"，而面试长度应当由
    `max_questions` 决定（见 `decide_next_action` 的说明）。
    两者不匹配时（例如只有一类材料却要问 8 题），
    主题会先被问完 —— 此时**不能结束面试**，而要再走一轮。

    新一轮的题不是简单重复：每道主题题都是一次**新的检索**
    （query 带上方向），因此会命中不同的材料片段，
    问题本身也不同。它保证的是"深浅交替"的节奏，
    而不是"同一批题重问"。

    轮次记在快照里，便于复盘"这场面试走了几轮"。
    """

    for topic in plan.topics:
        topic.status = "pending"

    plan.follow_ups_on_current = 0
    plan.passes += 1

    return plan.passes


def record_follow_up(plan: InterviewPlan) -> None:
    """记一次追问。"""

    plan.follow_ups_on_current += 1


def topic_query(
    plan: InterviewPlan,
    *,
    direction: str,
) -> str:
    """为当前主题构造检索 query。

    把"面试方向"与"本轮聚焦的材料类型"合在一起 ——
    检索仍然走原来的四类型检索，但提示词会据此聚焦，
    而 `plan_snapshot` 记录了"这一题是为哪个主题问的"。
    """

    topic = plan.current_topic
    label = topic.label if topic else "当前主题"

    return f"{direction}（本轮聚焦：{label}）"
