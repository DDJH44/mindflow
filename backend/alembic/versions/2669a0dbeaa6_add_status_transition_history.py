"""add status transition history

Revision ID: 2669a0dbeaa6
Revises: 1943f9f1110a
Create Date: 2026-10-08 18:15:56.597052

会话状态转移轨迹（MIND_FLOW_PLAN.md §10.6）。

会话表只保存**当前**状态，一旦状态变了就没有任何记录能回答
"这场面试什么时候从 asking 变成 evaluating"、"为什么停在这里"。
这让 `paused` 会话的审计与出错排查都变成不可查。

两个刻意的库层约束：

- `ck_status_history_no_self_transition`：禁止自转移。
  状态机本就拒绝它，在库层再拦一道，
  防止将来有人绕过服务层直接写表。
- `ix_status_history_session_created`：按会话按时间回放轨迹
  是最主要的查询方式，因此建复合索引而不是只索引 session_id。

无数据回填：迁移之前的历史状态变化没有留下任何记录，
无法重建也不应伪造。轨迹从本迁移之后开始积累。
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '2669a0dbeaa6'
down_revision: Union[str, Sequence[str], None] = '1943f9f1110a'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.create_table(
        'interview_status_history',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('session_id', sa.Integer(), nullable=False),
        sa.Column('from_status', sa.String(length=30), nullable=False),
        sa.Column('to_status', sa.String(length=30), nullable=False),
        sa.Column(
            'trigger',
            sa.String(length=40),
            server_default=sa.text("'unspecified'"),
            nullable=False,
        ),
        sa.Column(
            'created_at',
            sa.DateTime(),
            server_default=sa.text('NOW()'),
            nullable=False,
        ),
        sa.CheckConstraint(
            'from_status <> to_status',
            name='ck_status_history_no_self_transition',
        ),
        sa.ForeignKeyConstraint(
            ['session_id'],
            ['interview_sessions.id'],
            ondelete='CASCADE',
        ),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index(
        'ix_status_history_session_created',
        'interview_status_history',
        ['session_id', 'created_at'],
        unique=False,
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_index(
        'ix_status_history_session_created',
        table_name='interview_status_history',
    )
    op.drop_table('interview_status_history')
