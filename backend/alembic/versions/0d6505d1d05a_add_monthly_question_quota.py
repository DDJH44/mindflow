"""add monthly question quota

Revision ID: 0d6505d1d05a
Revises: 2669a0dbeaa6
Create Date: 2026-10-09 12:42:50.485978

按题计量所需的月度题目额度（MIND_FLOW_PLAN.md §25）。

为什么需要它：`InterviewSession.max_questions` 是**单场**上限，
描述"这一场想聊多深"，不是成本额度。
若只有单场上限，用户可以每场都设 30 题 —— 于是那个上限
反而变成了"允许 30 题"的授权，成本失控。

对应的用量指标 `question_generated` 无需建表迁移：
`interview_usage` 表已存在，指标只是 `metric` 列的一个新取值。

`server_default=100` 让已有用户直接拿到默认额度，
不需要单独的 UPDATE。取 100 的理由：
默认 10 场 × 默认 8 题 = 80，留约 25% 余量。
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '0d6505d1d05a'
down_revision: Union[str, Sequence[str], None] = '2669a0dbeaa6'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.add_column(
        'users',
        sa.Column(
            'monthly_question_quota',
            sa.Integer(),
            server_default=sa.text('100'),
            nullable=False,
        ),
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_column('users', 'monthly_question_quota')
