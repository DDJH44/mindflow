"""add session user_id and interview usage

Revision ID: 1943f9f1110a
Revises: 6567047c262f
Create Date: 2026-10-08 16:24:30.686628

三件事：

1. `interview_sessions.user_id` —— 会话所有者的冗余字段（§5.4）。
   已存在的会话从 `projects.owner_id` **回填**，
   因此分三步：先加可空列 → 回填 → 再设为 NOT NULL。
   直接加 NOT NULL 列会在非空表上失败。

2. `interview_usage` —— 按用户 / 账期 / 指标计量用量。
   唯一约束 (user_id, period, metric) 是"加了又加"语义的基础，
   配额校验依赖它做原子 UPSERT（见 usage_service.consume）。

3. `users.interview_quota` —— 每用户每月的面试场次上限。

关于外键命名：自动生成的迁移用 `None` 作为约束名，
PostgreSQL 会自己编一个名字，而 `downgrade` 里的
`op.drop_constraint(None, ...)` 将无法定位它 ——
因此这里显式命名，保证可回滚。
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '1943f9f1110a'
down_revision: Union[str, Sequence[str], None] = '6567047c262f'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


FK_SESSIONS_USER = "fk_interview_sessions_user_id_users"


def upgrade() -> None:
    """Upgrade schema."""

    # ---------------- 1. 用量表 ----------------
    op.create_table(
        'interview_usage',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('user_id', sa.Integer(), nullable=False),
        sa.Column('period', sa.String(length=7), nullable=False),
        sa.Column('metric', sa.String(length=40), nullable=False),
        sa.Column(
            'count',
            sa.Integer(),
            server_default=sa.text('0'),
            nullable=False,
        ),
        sa.Column('created_at', sa.DateTime(), nullable=False),
        sa.Column('updated_at', sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(
            ['user_id'],
            ['users.id'],
            ondelete='CASCADE',
        ),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint(
            'user_id',
            'period',
            'metric',
            name='uq_interview_usage_scope',
        ),
    )
    op.create_index(
        op.f('ix_interview_usage_id'),
        'interview_usage',
        ['id'],
        unique=False,
    )
    op.create_index(
        op.f('ix_interview_usage_user_id'),
        'interview_usage',
        ['user_id'],
        unique=False,
    )

    # ---------------- 2. 会话所有者（三步） ----------------
    #
    # 先加可空列，否则已有行会因为 NOT NULL 而无值可填。
    op.add_column(
        'interview_sessions',
        sa.Column('user_id', sa.Integer(), nullable=True),
    )

    # 从项目所有者回填。
    #
    # 这是本次迁移的**唯一数据推断**：迁移之前没有"谁创建了会话"
    # 的记录，而项目所有者是最接近的事实来源。
    # 若将来需要区分"创建者"与"项目所有者"，
    # 历史行只能承认这个近似。
    op.execute(
        """
        UPDATE interview_sessions AS s
        SET user_id = p.owner_id
        FROM projects AS p
        WHERE s.project_id = p.id
          AND s.user_id IS NULL
        """
    )

    # 确认回填完整。
    #
    # 有会话找不到项目所有者时**必须让迁移失败**，
    # 而不是留着 NULL 去撞 NOT NULL 约束 —— 后者报出的
    # 错误信息不会说明是哪几行有问题。
    connection = op.get_bind()
    orphans = connection.execute(
        sa.text(
            "SELECT COUNT(*) FROM interview_sessions "
            "WHERE user_id IS NULL"
        )
    ).scalar_one()

    if orphans:
        raise RuntimeError(
            f"有 {orphans} 条面试会话无法确定所有者："
            "其 project_id 找不到对应项目。"
            "请先修复这些数据再执行迁移。"
        )

    op.alter_column(
        'interview_sessions',
        'user_id',
        nullable=False,
    )

    op.create_index(
        op.f('ix_interview_sessions_user_id'),
        'interview_sessions',
        ['user_id'],
        unique=False,
    )
    op.create_foreign_key(
        FK_SESSIONS_USER,
        'interview_sessions',
        'users',
        ['user_id'],
        ['id'],
        ondelete='CASCADE',
    )

    # ---------------- 3. 配额 ----------------
    #
    # server_default 让已有用户直接拿到默认额度，
    # 不需要单独的 UPDATE。
    op.add_column(
        'users',
        sa.Column(
            'interview_quota',
            sa.Integer(),
            server_default=sa.text('10'),
            nullable=False,
        ),
    )


def downgrade() -> None:
    """Downgrade schema."""

    op.drop_column('users', 'interview_quota')

    op.drop_constraint(
        FK_SESSIONS_USER,
        'interview_sessions',
        type_='foreignkey',
    )
    op.drop_index(
        op.f('ix_interview_sessions_user_id'),
        table_name='interview_sessions',
    )
    op.drop_column('interview_sessions', 'user_id')

    op.drop_index(
        op.f('ix_interview_usage_user_id'),
        table_name='interview_usage',
    )
    op.drop_index(
        op.f('ix_interview_usage_id'),
        table_name='interview_usage',
    )
    op.drop_table('interview_usage')
