"""knowledge entities and relations

Revision ID: 7b4e9a1c6d38
Revises: 3f8c21d4a7b9
Create Date: 2026-10-11 15:20:00.000000

知识图谱第 1 步：实体与关系抽取（MIND_FLOW_PLAN.md §30.3）。

**为什么两张表存在 PostgreSQL 而不是 Neo4j**：
当前库只有 13 个 chunk、一份简历 5 块 —— 图查询的性能优势完全用不上，
而引入 Neo4j 是一个**新的有状态依赖**（要部署、要备份、要监控）。
这与 §28 拒绝引入 Redis 作队列是同一个判断标准：
新依赖要换来用得上的能力。
结构上按图设计（主体—谓词—客体），将来真要迁移时抽取结果可复用。

**`source_chunk_id` 是必需的，不是可选的**：
没有它，图谱里的每一句断言都无法追溯回材料，
而 §9.3 要求"资料型问题必须可追溯"。
图谱引入的事实必须能指回它来自哪一段 ——
否则它就成了一个无法核对的第二事实源。
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '7b4e9a1c6d38'
down_revision: Union[str, Sequence[str], None] = '3f8c21d4a7b9'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.create_table(
        'knowledge_entities',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('project_id', sa.Integer(), nullable=False),
        sa.Column('name', sa.String(length=200), nullable=False),
        sa.Column('entity_type', sa.String(length=40), nullable=False),
        # 该实体是从哪一段材料里抽出来的。追溯的依据。
        sa.Column('source_chunk_id', sa.Integer(), nullable=True),
        sa.Column(
            'created_at',
            sa.DateTime(),
            nullable=False,
            server_default=sa.text('now()'),
        ),
        sa.ForeignKeyConstraint(
            ['project_id'], ['projects.id'], ondelete='CASCADE'
        ),
        sa.ForeignKeyConstraint(
            ['source_chunk_id'],
            ['document_chunks.id'],
            # 材料被删除时，从它抽出的事实也应当消失 ——
            # 否则图谱会引用一段不存在的材料。
            ondelete='CASCADE',
        ),
        sa.PrimaryKeyConstraint('id'),
        # 同一项目里同名同类型只保留一条：抽取是幂等的，
        # 重复跑不该产生重复实体。
        sa.UniqueConstraint(
            'project_id', 'name', 'entity_type',
            name='uq_knowledge_entity',
        ),
    )

    # 按项目查询是唯一的读法（图谱永远限定在项目内）
    op.create_index(
        'ix_knowledge_entities_project',
        'knowledge_entities',
        ['project_id'],
    )

    op.create_table(
        'knowledge_relations',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('project_id', sa.Integer(), nullable=False),
        sa.Column('subject_id', sa.Integer(), nullable=False),
        # 谓词用字符串而不是枚举：
        # 材料里的关系种类是开放的（"负责"/"使用"/"掌握"…），
        # 枚举会在遇到新表述时逼着改表结构。
        sa.Column('predicate', sa.String(length=80), nullable=False),
        sa.Column('object_id', sa.Integer(), nullable=False),
        sa.Column('source_chunk_id', sa.Integer(), nullable=True),
        sa.Column(
            'created_at',
            sa.DateTime(),
            nullable=False,
            server_default=sa.text('now()'),
        ),
        sa.ForeignKeyConstraint(
            ['project_id'], ['projects.id'], ondelete='CASCADE'
        ),
        sa.ForeignKeyConstraint(
            ['subject_id'], ['knowledge_entities.id'], ondelete='CASCADE'
        ),
        sa.ForeignKeyConstraint(
            ['object_id'], ['knowledge_entities.id'], ondelete='CASCADE'
        ),
        sa.ForeignKeyConstraint(
            ['source_chunk_id'],
            ['document_chunks.id'],
            ondelete='CASCADE',
        ),
        sa.PrimaryKeyConstraint('id'),
        # 同一条三元组只保留一条，理由同上。
        sa.UniqueConstraint(
            'project_id', 'subject_id', 'predicate', 'object_id',
            name='uq_knowledge_relation',
        ),
    )

    op.create_index(
        'ix_knowledge_relations_project',
        'knowledge_relations',
        ['project_id'],
    )

    # 关系扩展检索要按**主体**找它的邻居，
    # 因此主体上的索引是查询路径的一部分，不是可选项。
    op.create_index(
        'ix_knowledge_relations_subject',
        'knowledge_relations',
        ['subject_id'],
    )
    op.create_index(
        'ix_knowledge_relations_object',
        'knowledge_relations',
        ['object_id'],
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_index(
        'ix_knowledge_relations_object',
        table_name='knowledge_relations',
    )
    op.drop_index(
        'ix_knowledge_relations_subject',
        table_name='knowledge_relations',
    )
    op.drop_index(
        'ix_knowledge_relations_project',
        table_name='knowledge_relations',
    )
    op.drop_table('knowledge_relations')

    op.drop_index(
        'ix_knowledge_entities_project',
        table_name='knowledge_entities',
    )
    op.drop_table('knowledge_entities')
