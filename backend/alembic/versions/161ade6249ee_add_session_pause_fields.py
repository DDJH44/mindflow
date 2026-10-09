"""add session pause fields

Revision ID: 161ade6249ee
Revises: c7d2e5f8a1b4
Create Date: 2026-10-08 13:40:51.665371

为"暂停 / 恢复"补齐三个字段（MIND_FLOW_PLAN.md §10）：

- resume_status：暂停必须记住"从哪来"。
  否则恢复时只能一律回到 asking，会把处于 waiting_for_answer
  的会话错误地拉回提问态。状态机据此动态计算 paused 的合法恢复目标。
- pause_reason：区分用户主动暂停与系统因错误暂停。
- paused_at：使"暂停了多久"**可以从数据推导**，
  而不是再存一个时长字段 —— 存下来的时长会随时间失真。

三个字段均可空，因此对已有数据无需回填。
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '161ade6249ee'
down_revision: Union[str, Sequence[str], None] = 'c7d2e5f8a1b4'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.add_column(
        'interview_sessions',
        sa.Column('resume_status', sa.String(length=30), nullable=True),
    )
    op.add_column(
        'interview_sessions',
        sa.Column('pause_reason', sa.String(length=200), nullable=True),
    )
    op.add_column(
        'interview_sessions',
        sa.Column('paused_at', sa.DateTime(), nullable=True),
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_column('interview_sessions', 'paused_at')
    op.drop_column('interview_sessions', 'pause_reason')
    op.drop_column('interview_sessions', 'resume_status')
