"""persist traceable evaluation findings

Revision ID: 4f8c1d2b7a90
Revises: 9e2b4c7a1f83
Create Date: 2026-10-27 00:20:00.000000

## 为什么要存

整场评价此前只产出三组字符串（优势 / 不足 / 建议）与一段 feedback。
它们读起来像结论，但**没有出处** —— 用户问「你凭什么说我技术深度
不够」，界面答不出来。

对"练习"这件事，可追溯性是刚需：一句「回答缺少取舍依据」如果不能
指到具体哪一轮，用户只能把它当成主观印象。

因此评价时让模型为关键结论标注**依据的轮次编号**，本地严格校验
（越界一律丢弃），落库后报告页可点击跳到对应问答。

## 为什么单独一列而不是塞进 `scoring_details`

两者回答不同问题：

- `scoring_details`：这个**分数**怎么来的（锚点、采样明细、口径快照）
- `findings`：这句**结论**凭什么这么说（依据哪几轮）

塞在一起会让"只读结论"的代码也不得不知道评分口径的细节。

## 为什么可空

本迁移之前的记录没有它。UI 据此隐藏该区块 —— 与
`interview_answers.analysis_snapshot` 的处理一致：
历史是事实，不伪造。
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '4f8c1d2b7a90'
down_revision: Union[str, Sequence[str], None] = '9e2b4c7a1f83'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.add_column(
        'interview_evaluations',
        sa.Column(
            'findings',
            sa.JSON(),
            nullable=True,
            comment=(
                '可追溯结论：[{kind, text, turns, verified}]。'
                'NULL 表示记录早于本功能'
            ),
        ),
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_column('interview_evaluations', 'findings')
