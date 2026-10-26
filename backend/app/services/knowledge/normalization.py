"""实体的归一化与**过滤**规则（纯逻辑，无 IO）。

## 为什么单独一个模块

`extraction_service._sanitize` 负责"模型给的东西能不能用"，
判断依据是**模型可能犯的错**（种类不对、重复、悬空引用）。

本模块负责另一类判断：**这些东西该不该进图谱**。依据是产品与合规，
不是模型质量：

- 手机号、邮箱、性别这类**联系方式**对面试没有价值
- 同一实体的不同**指称**（`MindFlow` / `MindFlow AI 面试助手`）
  会让图碎成多个节点，从而让"同一事实"在图上看起来更可信
- 谓词**不受控**时，关系无法被程序使用（`经过` 没法映射成任何检索逻辑）

放在这里而不是散在 `_sanitize` 里：这三类规则共同决定"图谱可用性"，
而它们都需要能**单独测试**（不调模型、不碰数据库）。

## 三条刻意的取舍

**① 别名用显式表，不做自动合并。**

自动判断"两个名字是不是同一个东西"只有两条路：语义相似度、
或"短名是长名子串"。前者会**编造不存在的合并**，后者会把
`RAG` 与 `RAG 知识库` 合成一个 —— 而它们是不同粒度的概念。

因此只认显式别名表。加新别名是**一行配置**，不是一次算法调整。

**② PII 按"谓词 + 值形态"双重识别。**

只看谓词会漏（模型可能写 `联系方式`、`电话`、`手机`），
只看值形态会误伤（`2023` 这种数字不是手机号）。两者都判，
命中任一即丢弃。

**③ 未知谓词保留原样，不归为"关联"。**

把所有未知谓词映射成 `关联` 会让关系**失去意义**
（`A --关联--> B` 等于没说），而保留原样至少人能读懂。
代价是谓词表会继续出现长尾，需要人工扩充 `PREDICATE_SYNONYMS`。
"""

import re

# ============================================================ 别名

# 显式别名表：归一化后的写法 → 规范名。
#
# 键必须是 `normalize_name` 的输出（去空白 + casefold）。
#
# 目前只有实测发现的这一对。**不要凭感觉往里加** ——
# 每加一条都是"我认为它们指同一个东西"的人工判断，
# 错了会让两个概念的错误合并传播到所有关系上。
ENTITY_ALIASES: dict[str, str] = {
    "mindflowai面试助手": "MindFlow",
    "ai面试助手": "MindFlow",
    "mindflow": "MindFlow",
}


def normalize_name(name: str) -> str:
    """归一化写法：移除全部空白并 casefold。

    与 `KnowledgeRepository._entity_key` 保持同一口径 —— 那是
    **合并键**，这里是**别名查表的键**。两者必须一致，
    否则"别名生效了但合并键没变"会导致同一实体重复入库。
    """

    return "".join(name.split()).casefold()


def canonical_entity_name(name: str) -> str:
    """按别名表把名字换成规范名。查不到则原样返回（去空白后）。"""

    cleaned = " ".join(name.split())

    if not cleaned:
        return ""

    return ENTITY_ALIASES.get(normalize_name(cleaned), cleaned)


# ============================================================ 谓词

# 谓词同义词 → 规范谓词。
#
# 只收**语义等价**的写法。语义相近但不同的（例如 `基于` 与 `使用`）
# 刻意不合并：`LangGraph Agent 基于 LangGraph` 与
# `X 使用 Y` 表达的是不同的关系强度，合并会把信息抹平。
PREDICATE_SYNONYMS: dict[str, str] = {
    "采用": "使用",
    "用到": "使用",
    "依赖": "使用",
    "使用着": "使用",
    "隶属于": "属于",
    "归属": "属于",
    "整合": "集成",
    "接入": "集成",
    "部署": "编排",
    "部署至": "编排",
    "持久化至": "存储于",
    "存储至": "存储于",
}


def canonical_predicate(predicate: str) -> str:
    """把谓词换成规范写法。未收录的原样返回。"""

    cleaned = " ".join(predicate.split())

    return PREDICATE_SYNONYMS.get(cleaned, cleaned)


# ============================================================ 隐私过滤

# 联系方式类谓词：这些关系**一律不入库**。
#
# 为什么：面试不会问"你的手机号是多少"，所以它们对产品零价值；
# 而它们会在「知识网络」里被原样展示 —— 等于把简历里的隐私
# 又抄了一份到另一个地方，扩大暴露面。
PII_PREDICATES = frozenset(
    {
        "联系电话",
        "电话",
        "手机",
        "手机号",
        "联系方式",
        "邮箱",
        "电子邮箱",
        "email",
        "微信",
        "qq",
        "住址",
        "地址",
        "现居",
        "籍贯",
        "性别",
        "年龄",
        "生日",
        "出生日期",
        "身份证",
        "身份证号",
        "政治面貌",
        "婚姻状况",
        "github",
        "git地址",
        "个人主页",
        "博客",
    }
)

# 值的形态特征。命中即视为联系方式。
#
# ⚠️ 数字模式刻意写紧：`\d{11}` 才当手机号，
# 否则 `2023`、`500` 这类正常数字会被误杀。
_PII_VALUE_PATTERNS = (
    # 手机号（中国大陆）
    re.compile(r"^1[3-9]\d{9}$"),
    # 邮箱
    re.compile(r"^[\w.+-]+@[\w-]+\.[\w.-]+$"),
    # URL / 个人主页 / GitHub
    re.compile(r"^(https?://|www\.)\S+$", re.IGNORECASE),
    re.compile(r"^[\w-]+\.(com|cn|io|net|org|me)(/\S*)?$", re.IGNORECASE),
    # 身份证号（15/18 位）
    re.compile(r"^\d{15}$|^\d{17}[\dXx]$"),
)


def looks_like_contact_value(value: str) -> bool:
    """值本身是否像联系方式（手机号/邮箱/网址/身份证）。"""

    text = value.strip()

    if not text:
        return False

    return any(pattern.match(text) for pattern in _PII_VALUE_PATTERNS)


def is_pii_predicate(predicate: str) -> bool:
    return normalize_name(predicate) in {
        normalize_name(item) for item in PII_PREDICATES
    }


def should_drop_relation(
    subject: str,
    predicate: str,
    obj: str,
) -> bool:
    """这条关系是否应当被丢弃（隐私）。

    三重判断，命中任一即丢：
    1. 谓词是联系方式类
    2. 宾语的值像联系方式
    3. 主语的值像联系方式（模型偶尔会把号码当主语）
    """

    if is_pii_predicate(predicate):
        return True

    return looks_like_contact_value(obj) or looks_like_contact_value(
        subject
    )
