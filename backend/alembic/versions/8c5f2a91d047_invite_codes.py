"""invite codes for registration control

Revision ID: 8c5f2a91d047
Revises: 7b4e9a1c6d38
Create Date: 2026-10-18 16:00:00.000000

公开部署的成本控制（MIND_FLOW_PLAN.md §39）。

**为什么需要这张表**：注册开放意味着任何陌生人都能给自己开一个
带「10 场面试 + 100 题」额度的账号，而每一道题都真实调用
**部署者的** LLM 与嵌入 API。限流管的是"单个人用多少"，
管不了"多少人可以用"。

**一次性码**：一条码 = 最多一个账号。因此成本可预算 ——
发 10 条码，最多少 100 场。

**`used_by_user_id` 既是"使用者"也是"是否已使用"**：
不另设 `is_used` 布尔列。两个字段表达同一件事，迟早会不一致
（D63/D70 的教训：同一个事实只应存在一处）。

**`ondelete="SET NULL"` 而不是 CASCADE**：删掉用户不该连带
删掉邀请记录 —— 那会让"已用掉的码"变回"可用"，
等于凭空多出一个后门。
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '8c5f2a91d047'
down_revision: Union[str, Sequence[str], None] = '7b4e9a1c6d38'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.create_table(
        'invite_codes',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('code', sa.String(length=64), nullable=False),
        sa.Column('note', sa.String(length=200), nullable=True),
        sa.Column('used_by_user_id', sa.Integer(), nullable=True),
        sa.Column('used_at', sa.DateTime(), nullable=True),
        sa.Column('expires_at', sa.DateTime(), nullable=True),
        sa.Column('created_at', sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(
            ['used_by_user_id'],
            ['users.id'],
            ondelete='SET NULL',
        ),
        sa.PrimaryKeyConstraint('id'),
    )

    op.create_index(
        op.f('ix_invite_codes_id'),
        'invite_codes',
        ['id'],
        unique=False,
    )
    # 唯一索引：码本身不能重复。
    # 它同时是"占码"并发安全的依据（见 invite_service.claim）。
    op.create_index(
        op.f('ix_invite_codes_code'),
        'invite_codes',
        ['code'],
        unique=True,
    )
    op.create_index(
        op.f('ix_invite_codes_used_by_user_id'),
        'invite_codes',
        ['used_by_user_id'],
        unique=False,
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_index(
        op.f('ix_invite_codes_used_by_user_id'),
        table_name='invite_codes',
    )
    op.drop_index(
        op.f('ix_invite_codes_code'),
        table_name='invite_codes',
    )
    op.drop_index(
        op.f('ix_invite_codes_id'),
        table_name='invite_codes',
    )
    op.drop_table('invite_codes')
