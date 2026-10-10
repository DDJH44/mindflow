"""document index jobs

Revision ID: 3f8c21d4a7b9
Revises: 0d6505d1d05a
Create Date: 2026-10-10 23:40:00.000000

异步文档索引的任务队列（MIND_FLOW_PLAN.md §28）。

**为什么用数据库当队列而不是 Redis**：Redis 已经在 Docker 里跑着，
看起来是"顺手的选择"。但它目前**完全没被业务代码使用**，
引入它意味着多一个有状态依赖、多一套故障模式。
而队列需要的两样东西 —— 事务与持久化 —— 数据库已经具备，
`FOR UPDATE SKIP LOCKED` 正是为这种场景设计的，
且天然支持多 worker 并发抢占而不重复。

**为什么 `document_id` 唯一**：一个文档同时只该有一个待处理任务。
重复入队会让同一份文档被嵌入两次 —— 白花钱，且**表面上完全正常**。
上传端点用 `ON CONFLICT DO NOTHING` 依赖这个约束做幂等。

**为什么有 `lease_expires_at`**：worker 领走任务后崩溃，
那条任务会永远停在 `running`。没有租约超时，
"任务卡住"会是最难查的一类故障 —— 界面上一直转圈、日志里没有报错。
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '3f8c21d4a7b9'
down_revision: Union[str, Sequence[str], None] = '0d6505d1d05a'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.create_table(
        'document_index_jobs',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('document_id', sa.Integer(), nullable=False),
        sa.Column(
            'status',
            sa.String(length=20),
            nullable=False,
            server_default=sa.text("'pending'"),
        ),
        sa.Column(
            'attempts',
            sa.Integer(),
            nullable=False,
            server_default=sa.text('0'),
        ),
        sa.Column('last_error', sa.Text(), nullable=True),
        sa.Column('lease_expires_at', sa.DateTime(), nullable=True),
        sa.Column(
            'created_at',
            sa.DateTime(),
            nullable=False,
            server_default=sa.text('now()'),
        ),
        sa.Column(
            'updated_at',
            sa.DateTime(),
            nullable=False,
            server_default=sa.text('now()'),
        ),
        sa.ForeignKeyConstraint(
            ['document_id'],
            ['documents.id'],
            ondelete='CASCADE',
        ),
        sa.PrimaryKeyConstraint('id'),
        # 一个文档同时只该有一个任务
        sa.UniqueConstraint('document_id'),
    )

    # 领取任务的查询按 (status, id) 排序，必须有索引，
    # 否则每次轮询都全表扫描。
    op.create_index(
        'ix_document_index_jobs_status_id',
        'document_index_jobs',
        ['status', 'id'],
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_index(
        'ix_document_index_jobs_status_id',
        table_name='document_index_jobs',
    )
    op.drop_table('document_index_jobs')
