"""验证实体/关系的质量规则（别名、谓词归一、隐私过滤）。

## 为什么必须单独测

这三条规则决定"什么能进图谱"，而它们**不依赖模型**——
是纯逻辑。也正因为不依赖模型，它们必须被钉死：
一旦被改坏，抽取照常跑、图照常画，只是内容悄悄变错。

实测触发本套件的两个现象：

1. **同一实体碎成两个节点**：`MindFlow` 与 `MindFlow AI 面试助手`
   各建一个，导致 `FastAPI / PostgreSQL / Redis` 的"使用"关系
   在图上重复出现两遍 —— 看起来像"两条独立证据"，其实是同一个
   事实被记了两次。**这比缺失更危险**，因为它伪装成更强的证据。
2. **联系方式被抽进图谱**：项目 29 的一轮 dry-run 出现过
   `黄家豪 --联系电话--> 19578961`、`--性别--> 男`、`--GitHub-->`。
   面试不会问手机号，而「知识网络」会把它们原样展示 ——
   等于把简历里的隐私又抄了一份到别处。

## 它验证

- 别名把不同指称合成一个规范名（含关系两端）
- 谓词同义写法合并，语义**不同**的（基于 vs 使用）不合并
- 联系方式按谓词与值形态**双重**识别；正常数字不被误杀
- 未知谓词保留原样（不归为"关联"）
- 清洗后仍保留原有行为：悬空引用丢弃、自环丢弃

用法：uv run python -m tests.test_knowledge_quality
"""

import sys

# ============================================================ 被测对象

from app.services.knowledge.extraction_service import (
    ExtractedEntity,
    ExtractedRelation,
    ExtractionResult,
    KnowledgeExtractionService,
)
from app.services.knowledge.normalization import (
    canonical_entity_name,
    canonical_predicate,
    looks_like_contact_value,
    should_drop_relation,
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


def run(root: str = r"D:\yuxi\MindFlow\backend") -> int:
    sys.path.insert(0, root)

    result = Result()

    print("=" * 78)
    print("知识图谱质量规则验证")
    print("=" * 78)

    # ================================================
    print()
    print("=" * 78)
    print("① 别名：不同指称 → 同一规范名")
    print("=" * 78)

    result.check(
        "「MindFlow AI 面试助手」→ MindFlow",
        canonical_entity_name("MindFlow AI 面试助手") == "MindFlow",
        canonical_entity_name("MindFlow AI 面试助手"),
    )
    result.check(
        "空格差异同样归一（「MindFlowAI面试助手」）",
        canonical_entity_name("MindFlowAI面试助手") == "MindFlow",
    )
    result.check(
        "未收录的名字原样返回（只去多余空白）",
        canonical_entity_name("  PostgreSQL  ") == "PostgreSQL",
    )

    # ⚠️ 这一条防"合并过头"：`RAG` 与 `RAG 知识库` 是不同粒度，
    # 把它们合成一个会制造一个"包含关系"的假象。
    result.check(
        "不做子串合并（RAG 与 RAG 知识库 保持不同）",
        canonical_entity_name("RAG 知识库") != canonical_entity_name("RAG"),
        f"{canonical_entity_name('RAG 知识库')} vs RAG",
    )

    # ================================================
    print()
    print("=" * 78)
    print("② 谓词归一：同义合并，异义保留")
    print("=" * 78)

    result.check("采用 → 使用", canonical_predicate("采用") == "使用")
    result.check("依赖 → 使用", canonical_predicate("依赖") == "使用")
    result.check("整合 → 集成", canonical_predicate("整合") == "集成")

    # `基于` 与 `使用` 表达不同强度，刻意不合并。
    result.check(
        "「基于」不被合并成「使用」（语义不同）",
        canonical_predicate("基于") == "基于",
    )
    result.check(
        "未知谓词保留原样，不归为「关联」",
        canonical_predicate("经过") == "经过",
        canonical_predicate("经过"),
    )

    # ================================================
    print()
    print("=" * 78)
    print("③ 隐私过滤：谓词与值形态双重识别")
    print("=" * 78)

    result.check(
        "电话谓词命中",
        should_drop_relation("黄家豪", "联系电话", "19578961"),
    )
    result.check(
        "邮箱谓词命中",
        should_drop_relation("黄家豪", "邮箱", "a@b.com"),
    )
    result.check(
        "谓词没写联系方式但值像手机号 → 命中",
        should_drop_relation("黄家豪", "相关", "13812345678"),
    )
    result.check(
        "值像邮箱 → 命中",
        looks_like_contact_value("someone@example.com"),
    )
    result.check(
        "值像网址 → 命中",
        looks_like_contact_value("github.com/DDJH44"),
    )

    # ⚠️ 防误杀：正常数字/技术名不该被当隐私。
    result.check(
        "「2023」不算联系方式（防误杀）",
        not looks_like_contact_value("2023"),
    )
    result.check(
        "「PostgreSQL」不算联系方式",
        not should_drop_relation("A", "使用", "PostgreSQL"),
    )
    result.check(
        "「500」不算联系方式",
        not looks_like_contact_value("500"),
    )

    # ================================================
    print()
    print("=" * 78)
    print("④ 端到端：_sanitize 同时完成三件事且不丢旧行为")
    print("=" * 78)

    raw = ExtractionResult(
        entities=[
            ExtractedEntity(name="MindFlow AI 面试助手", entity_type="system"),
            ExtractedEntity(name="MindFlow", entity_type="system"),
            ExtractedEntity(name="黄家豪", entity_type="person"),
            # 非法类型 → 归 other（旧行为）
            ExtractedEntity(name="某概念", entity_type="不存在的类型"),
            # 重复 → 去重
            ExtractedEntity(name="PostgreSQL", entity_type="technology"),
            ExtractedEntity(name="PostgreSQL", entity_type="technology"),
        ],
        relations=[
            # 别名两端都要能对上实体
            ExtractedRelation(
                subject="MindFlow AI 面试助手",
                predicate="采用",
                object="PostgreSQL",
            ),
            # 隐私 → 丢弃
            ExtractedRelation(
                subject="黄家豪",
                predicate="联系电话",
                object="19578961",
            ),
            # 悬空引用 → 丢弃（旧行为）
            ExtractedRelation(
                subject="MindFlow",
                predicate="使用",
                object="不存在的实体",
            ),
            # 自环 → 丢弃（旧行为）
            ExtractedRelation(
                subject="PostgreSQL",
                predicate="使用",
                object="PostgreSQL",
            ),
        ],
    )

    cleaned = KnowledgeExtractionService._sanitize(raw)

    entity_names = [item.name for item in cleaned.entities]

    print(f"      实体: {entity_names}")
    print(
        "      关系: "
        + str(
            [
                (r.subject, r.predicate, r.object)
                for r in cleaned.relations
            ]
        )
    )

    result.check(
        "两个指称合并成一个实体（不重复）",
        entity_names.count("MindFlow") == 1,
        f"MindFlow 出现 {entity_names.count('MindFlow')} 次",
    )
    result.check(
        "PostgreSQL 去重后只剩一个",
        entity_names.count("PostgreSQL") == 1,
    )
    result.check(
        "非法类型归为 other（保留旧行为）",
        any(
            item.name == "某概念" and item.entity_type == "other"
            for item in cleaned.entities
        ),
    )
    result.check(
        "别名两端能对上 → 关系被保留（没被引用校验误杀）",
        any(
            relation.subject == "MindFlow"
            and relation.object == "PostgreSQL"
            for relation in cleaned.relations
        ),
        str(
            [
                (r.subject, r.predicate, r.object)
                for r in cleaned.relations
            ]
        ),
    )
    result.check(
        "谓词被归一（采用 → 使用）",
        any(
            relation.predicate == "使用"
            for relation in cleaned.relations
        ),
    )
    result.check(
        "隐私关系被丢弃",
        not any(
            relation.predicate == "联系电话"
            for relation in cleaned.relations
        ),
    )
    result.check(
        "悬空引用仍被丢弃（旧行为）",
        not any(
            relation.object == "不存在的实体"
            for relation in cleaned.relations
        ),
    )
    result.check(
        "自环仍被丢弃（旧行为）",
        not any(
            relation.subject == relation.object
            for relation in cleaned.relations
        ),
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
    sys.exit(run())
