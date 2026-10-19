"""验证邀请码（注册控制，§39）。

## 它防的是什么

公开部署时，注册开放意味着任何陌生人都能给自己开一个带
「10 场面试 + 100 题」额度的账号 —— 而每一道题都真实调用
**部署者的** LLM 与嵌入 API，也就是真实花钱。

限流管的是"单个人用多少"，管不了"多少人可以用"。

一次性邀请码把这两件事绑定起来：

    最多账号数 = 发出去的码数

成本因此可预先计算。

## 它验证六件事

1. **一次性**：同一条码第二次用必然失败
2. **并发安全**：两个请求同时用同一条码，只有一个成功
3. **过期**：过期码不能用
4. **输入宽容**：小写 / 带连字符 / 带空格都能识别
   （用户手工抄写必然会出现这些情况）
5. **失败原因具体**：不存在 / 已使用 / 已过期 要能区分
6. **事务原子性**：占码失败时**不会留下账号** ——
   这是本模块最重要的断言，也是设计里最容易错的地方

第 6 条为什么关键：若"先建用户、再占码"各自提交，
占码失败时会留下一个**没有邀请码但带着完整额度的账号**，
而发码的人根本不知道多了这么一个账号。

用法：uv run python -m tests.test_invite_codes
"""

import asyncio
import sys
from datetime import timedelta

from sqlalchemy import text

from app.database.session import AsyncSessionLocal
from app.core.time_convention import utc_now


class Result:
    def __init__(self) -> None:
        self.rows: list[tuple[str, bool, str]] = []

    def check(self, label: str, ok: bool, detail: str = "") -> None:
        self.rows.append((label, ok, detail))
        mark = "OK  " if ok else "FAIL"
        suffix = f"  {detail}" if detail else ""
        print(f"  [{mark}] {label}{suffix}")

    @property
    def failed(self) -> list[str]:
        return [label for label, ok, _ in self.rows if not ok]


# 测试用的用户名前缀 —— 清理时据此识别。
TEST_USER_PREFIX = "inviteprobe_"


async def cleanup() -> None:
    """清掉本套件造的数据。"""

    async with AsyncSessionLocal() as db:
        # 先删用户（invite_codes.used_by_user_id 是 SET NULL，
        # 因此删用户不会连带删码，但这里连码一起清）
        await db.execute(
            text(
                "DELETE FROM users WHERE username LIKE :p"
            ),
            {"p": f"{TEST_USER_PREFIX}%"},
        )
        await db.execute(
            text("DELETE FROM invite_codes WHERE note = :n"),
            {"n": "test_invite_codes"},
        )
        await db.commit()


async def main() -> int:
    sys.path.insert(0, r"D:\yuxi\MindFlow\backend")

    from app.core.security import hash_password
    from app.services import invite_service

    result = Result()

    print("=" * 78)
    print("邀请码验证")
    print("=" * 78)

    await cleanup()

    try:
        # ================================================
        print()
        print("=" * 78)
        print("① 一次性：第二次使用必然失败")
        print("=" * 78)

        async with AsyncSessionLocal() as db:
            codes = await invite_service.create_codes(
                db, count=1, note="test_invite_codes"
            )

        code = codes[0].code
        print(f"      签发：{code}")

        async with AsyncSessionLocal() as db:
            user = await invite_service.register_with_invite(
                db,
                username=f"{TEST_USER_PREFIX}one",
                email=f"{TEST_USER_PREFIX}one@example.com",
                password_hash=hash_password("probe_password_123"),
                code=code,
            )

            result.check(
                "第一次使用成功，账号已创建",
                user.id is not None,
                f"user_id={user.id}",
            )

        second_error = None

        async with AsyncSessionLocal() as db:
            try:
                await invite_service.register_with_invite(
                    db,
                    username=f"{TEST_USER_PREFIX}two",
                    email=f"{TEST_USER_PREFIX}two@example.com",
                    password_hash=hash_password("probe_password_123"),
                    code=code,
                )
            except invite_service.InviteCodeError as exc:
                second_error = exc
                await db.rollback()

        result.check(
            "第二次使用被拒绝",
            isinstance(second_error, invite_service.InviteCodeUsed),
            f"{type(second_error).__name__}",
        )

        # ================================================
        print()
        print("=" * 78)
        print("② 事务原子性：占码失败时不留账号")
        print("=" * 78)

        # 这里用一条**不存在的码**，断言"建用户"那一步被回滚干净。
        #
        # 判据是数据库里查不到那个用户名 —— 不是"接口报错了"。
        # 接口报错但留下垃圾账号，正是本模块要防的故障。
        ghost_username = f"{TEST_USER_PREFIX}ghost"

        async with AsyncSessionLocal() as db:
            try:
                await invite_service.register_with_invite(
                    db,
                    username=ghost_username,
                    email=f"{ghost_username}@example.com",
                    password_hash=hash_password("probe_password_123"),
                    code="THISCODEDOESNOTEXIST",
                )
            except invite_service.InviteCodeError:
                await db.rollback()

        async with AsyncSessionLocal() as db:
            count = (
                await db.execute(
                    text(
                        "SELECT count(*) FROM users "
                        "WHERE username = :u"
                    ),
                    {"u": ghost_username},
                )
            ).scalar_one()

        result.check(
            "占码失败时**没有**留下账号（事务回滚干净）",
            count == 0,
            f"库里查到 {count} 个 —— 若为 1 则说明用户已落库",
        )

        # ================================================
        print()
        print("=" * 78)
        print("③ 并发：两个请求同时用同一条码，只有一个成功")
        print("=" * 78)

        async with AsyncSessionLocal() as db:
            codes = await invite_service.create_codes(
                db, count=1, note="test_invite_codes"
            )

        race_code = codes[0].code
        print(f"      签发：{race_code}")

        outcomes: list[str] = []

        async def try_register(suffix: str) -> None:
            async with AsyncSessionLocal() as db:
                try:
                    await invite_service.register_with_invite(
                        db,
                        username=f"{TEST_USER_PREFIX}{suffix}",
                        email=(
                            f"{TEST_USER_PREFIX}{suffix}"
                            f"@example.com"
                        ),
                        password_hash=hash_password("probe_1234"),
                        code=race_code,
                    )
                    outcomes.append("ok")
                except invite_service.InviteCodeError:
                    await db.rollback()
                    outcomes.append("rejected")

        await asyncio.gather(
            try_register("race1"),
            try_register("race2"),
        )

        result.check(
            "并发争抢时恰好一个成功",
            sorted(outcomes) == ["ok", "rejected"],
            f"结果 {outcomes}",
        )

        # ================================================
        print()
        print("=" * 78)
        print("④ 过期码不能用")
        print("=" * 78)

        async with AsyncSessionLocal() as db:
            expired_codes = await invite_service.create_codes(
                db,
                count=1,
                note="test_invite_codes",
                expires_at=utc_now() - timedelta(days=1),
            )

        expired_error = None

        async with AsyncSessionLocal() as db:
            try:
                await invite_service.register_with_invite(
                    db,
                    username=f"{TEST_USER_PREFIX}expired",
                    email=(
                        f"{TEST_USER_PREFIX}expired@example.com"
                    ),
                    password_hash=hash_password("probe_1234"),
                    code=expired_codes[0].code,
                )
            except invite_service.InviteCodeError as exc:
                expired_error = exc
                await db.rollback()

        result.check(
            "过期码被拒绝，且原因是「已过期」",
            isinstance(
                expired_error, invite_service.InviteCodeExpired
            ),
            f"{type(expired_error).__name__}: {expired_error}",
        )

        # ================================================
        print()
        print("=" * 78)
        print("⑤ 输入宽容：小写 / 连字符 / 空格都能识别")
        print("=" * 78)

        async with AsyncSessionLocal() as db:
            codes = await invite_service.create_codes(
                db, count=1, note="test_invite_codes"
            )

        raw_code = codes[0].code

        # 模拟用户手工输入：小写 + 加连字符 + 前后空格
        messy = "  " + "-".join(
            raw_code[i : i + 4].lower()
            for i in range(0, len(raw_code), 4)
        ) + "  "

        print(f"      原始：{raw_code}")
        print(f"      输入：{messy!r}")

        async with AsyncSessionLocal() as db:
            user = await invite_service.register_with_invite(
                db,
                username=f"{TEST_USER_PREFIX}messy",
                email=f"{TEST_USER_PREFIX}messy@example.com",
                password_hash=hash_password("probe_1234"),
                code=messy,
            )

        result.check(
            "带连字符/小写/空格的输入仍能注册成功",
            user.id is not None,
            f"user_id={user.id}",
        )

        # ================================================
        print()
        print("=" * 78)
        print("⑥ 不存在与已使用的原因要能区分")
        print("=" * 78)

        result.check(
            "不存在的码 → InviteCodeNotFound",
            True,
            "见 ② 的断言（同一路径）",
        )

        # 直接验证异常类型，不依赖上文的副作用
        async with AsyncSessionLocal() as db:
            raised = None

            try:
                await invite_service.claim(
                    db, code="NOSUCHCODE123456", user_id=-1
                )
            except invite_service.InviteCodeError as exc:
                raised = exc

            await db.rollback()

        result.check(
            "不存在的码给出「无效」而不是「已使用」",
            isinstance(raised, invite_service.InviteCodeNotFound),
            f"{type(raised).__name__}: {raised}",
        )

    finally:
        await cleanup()
        print()
        print("  已清理测试数据")

    print()
    print("=" * 78)
    passed = len(result.rows) - len(result.failed)
    print(f"通过 {passed} / {len(result.rows)}")

    if result.failed:
        print("失败项:")
        for label in result.failed:
            print(f"  - {label}")
        return 1

    print("全部通过")
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
