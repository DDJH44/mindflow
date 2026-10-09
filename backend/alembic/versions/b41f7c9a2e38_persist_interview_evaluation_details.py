"""persist interview evaluation details

Revision ID: b41f7c9a2e38
Revises: 8923b9720866
Create Date: 2026-10-08 01:40:00.000000

把整场评价的结构化内容与评分明细落库。

此前 strengths / weaknesses / suggestions 只存在于 LLM 返回值中，
没有持久化；而它们是 Phase 6「能力画像与训练建议」的前置数据。
同时新增 scoring_details 记录锚点与采样明细，
使历史分数可事后解释（线上与离线口径不同，见 ADR-021）。
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'b41f7c9a2e38'
down_revision: Union[str, Sequence[str], None] = '8923b9720866'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    # 已有行必须拿到可用的默认值，否则应用侧读取时
    # 会得到 NULL 而不是空列表，前端还要额外判空。
    op.add_column(
        'interview_evaluations',
        sa.Column(
            'strengths',
            sa.JSON(),
            nullable=True,
            server_default=sa.text("'[]'::json"),
        ),
    )
    op.add_column(
        'interview_evaluations',
        sa.Column(
            'weaknesses',
            sa.JSON(),
            nullable=True,
            server_default=sa.text("'[]'::json"),
        ),
    )
    op.add_column(
        'interview_evaluations',
        sa.Column(
            'suggestions',
            sa.JSON(),
            nullable=True,
            server_default=sa.text("'[]'::json"),
        ),
    )
    op.add_column(
        'interview_evaluations',
        sa.Column(
            'scoring_details',
            sa.JSON(),
            nullable=True,
            server_default=sa.text("'{}'::json"),
        ),
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_column('interview_evaluations', 'scoring_details')
    op.drop_column('interview_evaluations', 'suggestions')
    op.drop_column('interview_evaluations', 'weaknesses')
    op.drop_column('interview_evaluations', 'strengths')
