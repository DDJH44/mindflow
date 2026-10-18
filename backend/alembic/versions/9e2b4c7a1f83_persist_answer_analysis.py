"""persist per-turn answer analysis

Revision ID: 9e2b4c7a1f83
Revises: 8c5f2a91d047
Create Date: 2026-10-18 19:20:00.000000

## 为什么要存

每轮作答都会做一次分析（`InterviewAnswerAnalyzer`），产出
`missing_points`（漏了什么）、三个维度的档位、优点与建议。

而这份分析**此前算完就丢了**：只用来生成追问，然后随请求结束消失。

后果：
- 面试过程中不展示是对的（见下），但**结束后也看不到**
- 用户复盘时只能看到"我当时怎么答的"，看不到"我漏了什么" ——
  而对一个**面试练习**工具来说，后者才是学习价值所在
- 能力画像只能用整场评价，用不上逐题的细粒度数据

## 为什么不直接展示在面试过程中

`routers/interviews.py` 里有一条刻意的决定：

> 只回档位与缺口数量，不回完整分析。面试过程中把"技术深度偏低"
> 直接展示给候选人，会干扰后续作答 —— 他还没答完。

这条决定是对的，**本次不改**。本迁移只解决"数据被丢弃"，
展示放到**结束之后的报告页**（复盘模式）。

## 为什么是 JSON 快照而不是拆成多张表

这份数据是**某一次分析的完整留档**，不需要按字段查询、
不需要关联、也不会被更新。JSON 列最合适。

而且它天然带版本问题：分析器的提示词或档位定义变了，
旧记录仍应保持"当时的判定"。快照正好表达这一点。

## 为什么可空

既有记录没有这份数据。历史面试（本迁移之前的）在复盘时
只能看到问答本身 —— 那是事实，不该伪造。
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '9e2b4c7a1f83'
down_revision: Union[str, Sequence[str], None] = '8c5f2a91d047'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.add_column(
        'interview_answers',
        sa.Column(
            'analysis_snapshot',
            sa.JSON(),
            nullable=True,
            comment=(
                '逐轮分析的留档（缺口/档位/建议）。'
                'NULL 表示该记录早于本功能，或分析失败'
            ),
        ),
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_column('interview_answers', 'analysis_snapshot')
