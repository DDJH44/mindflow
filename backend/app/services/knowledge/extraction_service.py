"""知识图谱第 1 步：从材料片段里抽取实体与关系（§30.3）。

## 一次调用同时抽实体与关系

也可以分成两次调用（先抽实体、再抽关系），但那样：
- 成本翻倍（同一段材料要过两次模型）
- 第二遍拿不到第一遍的实体列表，**关系容易引用到不存在的实体**

因此一次调用返回两者，并在本地做一次引用校验
（见 `_sanitize`）—— 模型偶尔会写出没在实体列表里的名词，
那属于必须丢弃的脏数据，而不是可以放进图里的边。

## 抽取质量是这套机制的地基

图谱里的每一句断言都要能追溯回材料（`source_chunk_id`），
因为 §9.3 要求"资料型问题必须可追溯"。
抽取错了，后面所有环节都在错误的基础上工作 ——
而那种错误**不会报错**，只会让检索带回不相干的片段。
因此 §30.5 把"抽样人工核对准确率"列为验收项之一。
"""

from pydantic import BaseModel, Field

from app.core.logging import get_logger
from app.services.knowledge.normalization import (
    canonical_entity_name,
    canonical_predicate,
    should_drop_relation,
)
from app.core.model_json import (
    ModelJSONParseError,
    parse_model_json,
)
from app.services.openai_llm_service import OpenAILLMService

logger = get_logger("app.knowledge_extraction")


# 允许的实体种类。
#
# 用固定集合而不是让模型自由生成：自由生成会得到
# "技术栈"/"tech"/"technology" 这类同义异形，
# 让后续按种类过滤变得不可靠。
#
# ⚠️ `person` 与 `other` **必须存在**（实测教训）：
# 第一版漏了它们，于是候选人的姓名"张明"被归成 `skill`（技能）——
# 明显不对。原因有两个：没有合适的种类可选，
# 而本地兜底又把未知种类硬编码成 `skill`。
# 兜底改成 `other` 且种类里有 `person` 之后才正确。
ENTITY_TYPES = (
    "person",       # 人（候选人、面试官）
    "skill",        # 技能、能力
    "technology",   # 具体技术、框架、工具
    "system",       # 系统、平台、模块
    "organization", # 公司、学校、团队
    "role",         # 角色、职责
    "experience",   # 项目、经历
    "achievement",  # 成果、指标
    "other",        # 无法归入以上任一类
)


class ExtractedEntity(BaseModel):
    """一个实体。"""

    name: str = Field(
        description="实体的规范名称，尽量与材料中的写法一致"
    )
    entity_type: str = Field(
        description=f"必须是以下之一：{', '.join(ENTITY_TYPES)}"
    )


class ExtractedRelation(BaseModel):
    """一条关系（主体 — 谓词 — 客体）。"""

    subject: str = Field(description="主体实体的名称")
    predicate: str = Field(
        description="关系的简短动词短语，例如「负责」「使用」「掌握」"
    )
    object: str = Field(description="客体实体的名称")


class ExtractionResult(BaseModel):
    """一次抽取的完整结果。"""

    entities: list[ExtractedEntity] = Field(default_factory=list)
    relations: list[ExtractedRelation] = Field(default_factory=list)


SYSTEM_PROMPT = """你是知识抽取器。你的任务是从候选人的材料片段里
抽出**结构化事实**，供后续把材料里的信息关联起来使用。

严格遵守：

1. 只抽**材料里明确写了**的内容。不要根据常识补全，
   不要推断材料没有说的关系。宁可少抽，不可编造。
2. 实体名称用材料里的原词或最简规范形式
   （例如"PostgreSQL"不要写成"postgres 数据库"）。
3. 关系的主体和客体**必须**是你抽出的实体名称之一，
   不要写出实体列表之外的名词。
4. 谓词用简短动词短语（2–6 字），例如「负责」「使用」
   「掌握」「参与」「擅长」「毕业于」。
5. 材料是简历/岗位描述/项目说明/代码片段，可能没有关系 ——
   那就只返回实体，relations 给空列表。

只返回 JSON，不要解释。格式：

{
  "entities": [{"name": "PostgreSQL", "entity_type": "technology"}],
  "relations": [
    {"subject": "MindFlow", "predicate": "使用", "object": "PostgreSQL"}
  ]
}
"""


class KnowledgeExtractionService:
    """把一段材料片段抽成实体与关系。"""

    def __init__(self):
        self.llm_service = OpenAILLMService()

    async def extract(
        self,
        content: str,
    ) -> ExtractionResult:
        """抽取。失败时抛出 `ModelJSONParseError` 或上游异常。

        刻意**不吞异常**：调用方（后台任务）需要知道失败，
        才能决定重试。静默返回空结果会让"抽取失败"表现为
        "这段材料没有实体"，而两者完全不同。
        """

        if not content.strip():
            return ExtractionResult()

        prompt = (
            "请从下面这段材料里抽取实体与关系。\n\n"
            f"--- 材料开始 ---\n{content}\n--- 材料结束 ---"
        )

        raw = await self.llm_service.generate(
            prompt=prompt,
            system_prompt=SYSTEM_PROMPT,
            # 温度取 0：抽取是**事实提取**，不是创作。
            # 同一段材料两次抽出不同结果会让图谱不可复现，
            # 而评审时无法判断差异来自材料还是模型随机性。
            temperature=0.0,
            max_tokens=2000,
        )

        try:
            result = parse_model_json(raw, ExtractionResult)
        except ModelJSONParseError as exc:
            logger.warning("抽取结果无法解析：%s", str(exc)[:200])
            raise

        return self._sanitize(result)

    @staticmethod
    def _sanitize(result: ExtractionResult) -> ExtractionResult:
        """清掉脏数据。

        三件事，都是实测模型确实会犯的：

        1. **种类不在允许集合里** → 归为 `other` 而不是丢弃。
           丢弃会静默少抽，而归错种类只是分类不准 ——
           后者危害小得多。
           （兜底**不能用** `skill`：第一版这么做时，
           候选人的姓名因为没有合适的种类可选，被归成了"技能"。）
        2. **名称重复** → 去重（同一段材料里同一个词出现多次）。
        3. **关系引用了不存在的实体** → 丢弃该关系。
           一个指向不存在节点的边会让图查询拿到断链，
           而那种错误在检索时表现为"关联不到任何东西"，
           很难归因到抽取环节。
        """

        entities: list[ExtractedEntity] = []
        seen: set[tuple[str, str]] = set()

        for entity in result.entities:
            # 别名归一：先把不同指称换成同一个规范名，
            # 否则同一实体在图里碎成多个节点（见 normalization 模块）。
            name = canonical_entity_name(entity.name.strip())

            if not name:
                continue

            entity_type = entity.entity_type.strip().lower()

            if entity_type not in ENTITY_TYPES:
                # 保留但归类：见上方说明 1
                entity_type = "other"

            key = (name, entity_type)

            if key in seen:
                continue

            seen.add(key)
            entities.append(
                ExtractedEntity(name=name, entity_type=entity_type)
            )

        names = {entity.name for entity in entities}

        relations: list[ExtractedRelation] = []
        seen_relations: set[tuple[str, str, str]] = set()

        for relation in result.relations:
            # 关系的两端也要走别名，否则"实体叫 MindFlow、关系里写
            # MindFlow AI 面试助手"会让下面的引用校验把它丢掉。
            subject = canonical_entity_name(relation.subject.strip())
            obj = canonical_entity_name(relation.object.strip())

            # 谓词归一：同义写法合并，让关系能被程序按谓词聚合。
            predicate = canonical_predicate(relation.predicate.strip())

            if not subject or not predicate or not obj:
                continue

            # 隐私过滤：联系方式类关系一律不入库。
            #
            # 面试不会问手机号，所以它对产品零价值；而「知识网络」
            # 会把它原样展示 —— 等于把简历里的隐私又抄一份到别处。
            if should_drop_relation(subject, predicate, obj):
                continue

            # 自环没有意义（"PostgreSQL 使用 PostgreSQL"）
            if subject == obj:
                continue

            # 引用校验：见上方说明 3
            if subject not in names or obj not in names:
                continue

            key = (subject, predicate, obj)

            if key in seen_relations:
                continue

            seen_relations.add(key)

            relations.append(
                ExtractedRelation(
                    subject=subject,
                    predicate=predicate,
                    object=obj,
                )
            )

        return ExtractionResult(
            entities=entities, relations=relations
        )
