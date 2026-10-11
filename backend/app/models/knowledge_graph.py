from datetime import datetime

from sqlalchemy import (
    DateTime,
    ForeignKey,
    Integer,
    String,
    UniqueConstraint,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.database.base import Base


class KnowledgeEntity(Base):
    """从材料里抽出的实体（人、技术、系统、经历…）。

    对应 MIND_FLOW_PLAN.md §30.3 第 1 步。

    `source_chunk_id` **不是可选的元数据**：没有它，
    图谱里的每一句断言都无法追溯回材料，而 §9.3 要求
    "资料型问题必须可追溯"。图谱引入的事实必须能指回
    它来自哪一段，否则它就成了一个无法核对的第二事实源。
    """

    __tablename__ = "knowledge_entities"

    __table_args__ = (
        UniqueConstraint(
            "project_id",
            "name",
            "entity_type",
            name="uq_knowledge_entity",
        ),
    )

    id: Mapped[int] = mapped_column(
        Integer, primary_key=True, autoincrement=True
    )

    project_id: Mapped[int] = mapped_column(
        Integer,
        ForeignKey("projects.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )

    name: Mapped[str] = mapped_column(String(200), nullable=False)

    # 实体种类。用字符串而不是枚举：材料里的种类是开放的
    # （技能 / 系统 / 组织 / 经历…），枚举会在遇到新表述时
    # 逼着改表结构，而图谱的价值恰恰在于容纳多样性。
    entity_type: Mapped[str] = mapped_column(
        String(40), nullable=False
    )

    source_chunk_id: Mapped[int | None] = mapped_column(
        Integer,
        ForeignKey("document_chunks.id", ondelete="CASCADE"),
        nullable=True,
    )

    created_at: Mapped[datetime] = mapped_column(
        DateTime, nullable=False, server_default=func.now()
    )


class KnowledgeRelation(Base):
    """实体之间的关系（主体 — 谓词 — 客体）。

    **结构上按图设计**，但存 PostgreSQL 而不是 Neo4j ——
    当前规模用不上图查询的性能优势，而引入 Neo4j 是一个
    新的有状态依赖。这与 §28 拒绝引入 Redis 作队列
    是同一个判断标准：新依赖要换来用得上的能力。
    真要迁移时，这里的抽取结果可以复用。
    """

    __tablename__ = "knowledge_relations"

    __table_args__ = (
        UniqueConstraint(
            "project_id",
            "subject_id",
            "predicate",
            "object_id",
            name="uq_knowledge_relation",
        ),
    )

    id: Mapped[int] = mapped_column(
        Integer, primary_key=True, autoincrement=True
    )

    project_id: Mapped[int] = mapped_column(
        Integer,
        ForeignKey("projects.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )

    subject_id: Mapped[int] = mapped_column(
        Integer,
        ForeignKey("knowledge_entities.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )

    # 谓词用字符串而不是枚举（理由同实体种类）。
    predicate: Mapped[str] = mapped_column(
        String(80), nullable=False
    )

    object_id: Mapped[int] = mapped_column(
        Integer,
        ForeignKey("knowledge_entities.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )

    source_chunk_id: Mapped[int | None] = mapped_column(
        Integer,
        ForeignKey("document_chunks.id", ondelete="CASCADE"),
        nullable=True,
    )

    created_at: Mapped[datetime] = mapped_column(
        DateTime, nullable=False, server_default=func.now()
    )
