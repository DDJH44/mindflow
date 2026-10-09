"""add evidence and state machine fields

Revision ID: c7d2e5f8a1b4
Revises: b41f7c9a2e38
Create Date: 2026-10-08 02:10:00.000000

两件事合并为一次 schema 变更（都动面试相关的两张表）：

1. 资料依据（§9.3 提问原则）
   - interview_questions.evidence_chunk_ids
   - interview_questions.is_general

2. 面试状态机（§10 / ADR-010）
   - interview_sessions.status 宽度 20 → 30，默认值 created → draft
   - interview_sessions.target_role
   - interview_sessions.plan_snapshot

关于 status 的兼容：
旧值 "created" 语义等于新状态机的 draft，
由 interview_state_machine.LEGACY_STATUS_MAP 解读，
因此**不做数据回填** —— 回填会改写历史记录，
而历史状态本身是可审计信息的一部分。
新写入的行一律使用 draft。
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'c7d2e5f8a1b4'
down_revision: Union[str, Sequence[str], None] = 'b41f7c9a2e38'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""

    # ---------------- 1. 资料依据 ----------------

    op.add_column(
        'interview_questions',
        sa.Column(
            'evidence_chunk_ids',
            sa.JSON(),
            nullable=True,
            server_default=sa.text("'[]'::json"),
        ),
    )
    op.add_column(
        'interview_questions',
        sa.Column(
            'is_general',
            sa.Boolean(),
            nullable=False,
            server_default=sa.text('false'),
        ),
    )

    # ---------------- 2. 状态机 ----------------

    # 状态名最长是 "preparing_context"（17 字符），
    # 但留出余量避免将来加状态又要改宽度。
    op.alter_column(
        'interview_sessions',
        'status',
        existing_type=sa.String(length=20),
        type_=sa.String(length=30),
        existing_nullable=False,
        existing_server_default=None,
    )

    # 新建会话的默认状态改为 draft（状态机起点）。
    # 注意：这只影响**新行**；已有行的 "created" 保持不变，
    # 由 LEGACY_STATUS_MAP 在读取时兼容。
    op.alter_column(
        'interview_sessions',
        'status',
        existing_type=sa.String(length=30),
        server_default=sa.text("'draft'"),
    )

    op.add_column(
        'interview_sessions',
        sa.Column(
            'target_role',
            sa.String(length=100),
            nullable=True,
        ),
    )
    op.add_column(
        'interview_sessions',
        sa.Column(
            'plan_snapshot',
            sa.JSON(),
            nullable=True,
            server_default=sa.text("'{}'::json"),
        ),
    )


def downgrade() -> None:
    """Downgrade schema."""

    op.drop_column('interview_sessions', 'plan_snapshot')
    op.drop_column('interview_sessions', 'target_role')

    op.alter_column(
        'interview_sessions',
        'status',
        existing_type=sa.String(length=30),
        server_default=None,
    )
    op.alter_column(
        'interview_sessions',
        'status',
        existing_type=sa.String(length=30),
        type_=sa.String(length=20),
        existing_nullable=False,
    )

    op.drop_column('interview_questions', 'is_general')
    op.drop_column('interview_questions', 'evidence_chunk_ids')
