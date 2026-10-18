"""邀请码的签发、校验与原子占用。

## 最关键的一条：占码必须与建用户**在同一个事务里**

这条不是洁癖，而是两种真实故障：

**故障 A：先建用户、再占码（各自提交）**
若占码失败（码刚好被别人抢走），用户已经建好了。
结果：一个没有邀请码的账号存在库里 —— 它带着完整的
10 场额度，而发码的人不知道多了一个账号。

**故障 B：先占码、再建用户（各自提交）**
若建用户失败（用户名重复），码已经被标记成"已使用"。
结果：发出去的码**凭空作废**，而那个人永远注册不了。
这种故障最难解释 —— 用户说"码没用过但提示已使用"，
你查库只会看到它确实被用了。

因此正确做法是：**一个事务里 flush 出 user.id，
原子占码，一起提交**。任何一步失败都整体回滚。

## 并发安全：两个人同时用同一个码

`UPDATE ... WHERE used_by_user_id IS NULL RETURNING` 是
**条件更新**：数据库保证只有一个事务能把 NULL 改成非 NULL。
第二个会阻塞到第一个提交，然后看到已经不是 NULL，
于是 `rowcount = 0` → 判定失败。

不用"先 SELECT 判断再 UPDATE"—— 那中间有窗口，
两个请求会同时看到"未使用"并一起通过。
"""

import secrets
from datetime import datetime

from sqlalchemy import or_, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.logging import get_logger
from app.core.time_convention import utc_now
from app.models.invite_code import InviteCode
from app.repositories.user_repository import UserRepository

logger = get_logger("app.invite")

# 生成码用的字符集。
#
# **刻意去掉易混字符**（0/O、1/I/l）：
# 邀请码是**人手工抄写**的（微信、口头、手写纸条），
# 而 `0` 与 `O` 抄错一次就注册失败，且失败信息无法解释。
#
# 这不是审美，是可操作性问题。
CODE_ALPHABET = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"

# 码长。
#
# 16 字符 × 32 个字符集 ≈ 2^80 组合，
# 暴力猜中的概率可以忽略。
# 同时保持在"能读、能抄"的长度内。
CODE_LENGTH = 16


class InviteCodeError(Exception):
    """邀请码无效。

    用异常而不是返回布尔值：调用方必须给出**具体原因**
    （不存在 / 已使用 / 已过期），否则用户会感到
    "明明有码却注册不了"而无法自助解决。
    """


class InviteCodeRequired(InviteCodeError):
    """本次部署要求邀请码，但请求里没带。"""


class InviteCodeNotFound(InviteCodeError):
    """码不存在（或已被撤销）。"""


class InviteCodeUsed(InviteCodeError):
    """码已被使用过。"""


class InviteCodeExpired(InviteCodeError):
    """码已过期。"""


def generate_code() -> str:
    """生成一条随机邀请码（16 字符，去掉易混字符）。"""

    return "".join(
        secrets.choice(CODE_ALPHABET) for _ in range(CODE_LENGTH)
    )


def normalize_code(raw: str) -> str:
    """规范化用户输入的码。

    用户会怎么输入邀请码：
    - 带空格（从表格里复制）
    - 小写（手机输入法自动小写）
    - 中间加连字符（自己为了好读而分隔）

    因此**全部吃掉**，只比较字符内容。若照着原始输入比较，
    用户会看到"邀请码无效"，而那个码其实是对的 ——
    这类失败最消耗信任。
    """

    return "".join(
        char for char in raw.strip().upper() if char.isalnum()
    )


async def create_codes(
    db: AsyncSession,
    *,
    count: int,
    note: str | None = None,
    expires_at: datetime | None = None,
) -> list[InviteCode]:
    """批量签发邀请码。

    `count` 是**本次最多能多几个账号** —— 这个对应关系是刻意
    保持的：一次性码让成本可预算（见模块 `invite_code.py` 的说明）。
    """

    if count <= 0:
        raise ValueError("count 必须大于 0")

    codes: list[InviteCode] = []

    for _ in range(count):
        # 极小概率撞码：唯一索引会拒绝，重试即可。
        # 10 次都撞上说明随机源出了问题，让异常暴露出来。
        for attempt in range(10):
            code = generate_code()

            exists = (
                await db.execute(
                    select(InviteCode.id).where(
                        InviteCode.code == code
                    )
                )
            ).scalar_one_or_none()

            if exists is None:
                break
        else:  # pragma: no cover - 随机源异常
            raise RuntimeError(
                "连续 10 次生成到重复邀请码，随机源可能有问题"
            )

        codes.append(
            InviteCode(
                code=code,
                note=note,
                expires_at=expires_at,
            )
        )

    db.add_all(codes)
    await db.commit()

    for item in codes:
        await db.refresh(item)

    logger.info(
        "签发了 %d 条邀请码（备注：%s）", len(codes), note
    )

    return codes


async def claim(
    db: AsyncSession,
    *,
    code: str,
    user_id: int,
) -> InviteCode:
    """原子占用一条邀请码。

    **调用前不得提交事务** —— 但 `user_id` 来自 flush，
    因此调用方需要先 flush 出用户 id 再调本函数，
    最后统一提交（见 `app/routers/auth.py`）。

    失败时抛 `InviteCodeError` 的子类，且**不得**在这里
    提交或回滚 —— 事务边界由调用方掌握。
    """

    normalized = normalize_code(code)

    if not normalized:
        raise InviteCodeRequired("邀请码不能为空")

    now = utc_now()

    # 条件更新：只有"未使用且未过期"的行会被改。
    #
    # 用 `update()` 而不是"先查再改"，因为后者有并发窗口：
    # 两个请求会同时看到 used_by_user_id IS NULL 并一起通过。
    result = await db.execute(
        update(InviteCode)
        .where(
            InviteCode.code == normalized,
            InviteCode.used_by_user_id.is_(None),
            or_(
                InviteCode.expires_at.is_(None),
                InviteCode.expires_at > now,
            ),
        )
        .values(used_by_user_id=user_id, used_at=now)
        .returning(InviteCode.id)
    )

    claimed_id = result.scalar_one_or_none()

    if claimed_id is not None:
        return (
            await db.execute(
                select(InviteCode).where(
                    InviteCode.id == claimed_id
                )
            )
        ).scalar_one()

    # 没占到 —— 需要给出**具体原因**。
    #
    # 用户在"码没用过却说已使用"和"码过期了"这两种情况下
    # 该做的事完全不同（前者找我补码，后者自己也看得出）。
    # 笼统地说"无效"会迫使双方来回沟通。
    existing = (
        await db.execute(
            select(InviteCode).where(
                InviteCode.code == normalized
            )
        )
    ).scalar_one_or_none()

    if existing is None:
        raise InviteCodeNotFound("邀请码无效，请检查是否抄错")

    if existing.used_by_user_id is not None:
        raise InviteCodeUsed("这条邀请码已被使用")

    if existing.expires_at is not None and existing.expires_at <= now:
        raise InviteCodeExpired("这条邀请码已过期")

    # 走到这里说明条件更新失败但上面的原因都对不上 ——
    # 可能是并发窗口里的极小概率竞态，如实报"已被使用"。
    raise InviteCodeUsed("这条邀请码已被使用")


async def register_with_invite(
    db: AsyncSession,
    *,
    username: str,
    email: str,
    password_hash: str,
    code: str,
):
    """用邀请码注册一个账号（**一个事务内完成**）。

    顺序很重要：
    1. `flush` 出 user.id（**不提交**）
    2. 原子占码
    3. 一起提交

    任何一步失败都整体回滚 —— 不会留下"无码账号"，
    也不会白白作废一条码。理由见模块文档。
    """

    user_repository = UserRepository(db)

    # 1. flush 出 id，但**不提交**
    user = await user_repository.add(
        username=username,
        email=email,
        password_hash=password_hash,
    )

    # 2. 原子占码。失败时抛异常，调用方回滚 →
    #    上面那条 INSERT 也随之撤销。
    await claim(db, code=code, user_id=user.id)

    # 3. 一起提交
    await db.commit()

    await db.refresh(user)

    logger.info(
        "新账号注册成功（user_id=%s，邀请码已消耗）", user.id
    )

    return user


async def list_codes(
    db: AsyncSession,
    *,
    include_used: bool = True,
) -> list[InviteCode]:
    """列出邀请码（管理用）。"""

    statement = select(InviteCode).order_by(InviteCode.id.desc())

    if not include_used:
        statement = statement.where(
            InviteCode.used_by_user_id.is_(None)
        )

    return list((await db.execute(statement)).scalars().all())


async def revoke(db: AsyncSession, *, code: str) -> bool:
    """撤销一条**未使用**的邀请码。返回是否真的撤销了。

    已使用的码**不删** —— 它是注册记录的一部分：
    删掉就说不清"这个账号是怎么来的"。
    """

    normalized = normalize_code(code)

    result = await db.execute(
        select(InviteCode).where(
            InviteCode.code == normalized,
            InviteCode.used_by_user_id.is_(None),
        )
    )

    invite = result.scalar_one_or_none()

    if invite is None:
        return False

    await db.delete(invite)
    await db.commit()

    logger.info("已撤销邀请码 %s", normalized)

    return True
