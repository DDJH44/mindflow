"""add question budget and termination

Revision ID: 6567047c262f
Revises: 161ade6249ee
Create Date: 2026-10-08 16:01:16.163433

为"题目预算与终止条件"补齐三个字段（MIND_FLOW_PLAN.md §9.3）：

- max_questions：题目预算上限（含追问）。
  追问也计入预算，因为它同样消耗一次 LLM 分析 + 一次生成；
  若不计入，一条长追问链能把成本推到远超预期。
- questions_asked：已问过的题目总数（含追问），预算计数的依据。
  不复用 current_question_index：后者是"下一个要分配的索引"，
  语义不同，合并后一旦索引跳过或重复，预算判断会跟着错。
- termination_reason：结束原因（见 interview_termination）。

`max_questions` 与 `questions_asked` 都带 server_default，
因此新增 NOT NULL 列时已有行会拿到 8 / 0 ——
不加默认值会让这条迁移在非空表上直接失败。
`termination_reason` 可空，历史会话本就"没有记录结束原因"，
不必伪造一个。
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '6567047c262f'
down_revision: Union[str, Sequence[str], None] = '161ade6249ee'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.add_column(
        'interview_sessions',
        sa.Column(
            'max_questions',
            sa.Integer(),
            server_default=sa.text('8'),
            nullable=False,
        ),
    )
    op.add_column(
        'interview_sessions',
        sa.Column(
            'questions_asked',
            sa.Integer(),
            server_default=sa.text('0'),
            nullable=False,
        ),
    )
    op.add_column(
        'interview_sessions',
        sa.Column(
            'termination_reason',
            sa.String(length=30),
            nullable=True,
        ),
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_column('interview_sessions', 'termination_reason')
    op.drop_column('interview_sessions', 'questions_asked')
    op.drop_column('interview_sessions', 'max_questions')
