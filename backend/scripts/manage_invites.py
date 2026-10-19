"""邀请码管理 CLI。

## 用法

    # 签发 5 条码，备注"给朋友"，7 天后过期
    uv run python -m scripts.manage_invites create --count 5 \\
        --note "内测朋友" --expires-days 7

    # 列出现有码
    uv run python -m scripts.manage_invites list

    # 只看还能用的
    uv run python -m scripts.manage_invites list --unused

    # 撤销一条（仅限未使用的）
    uv run python -m scripts.manage_invites revoke ABCD2345...

## 为什么用 CLI 而不是网页后台

一个后台页面意味着：一个新的路由、一个新的权限判定、
一个新的被攻击面 —— 而"发邀请码"这件事**低频且敏感**，
放在服务器上敲命令是最小的暴露面。

用户看不到它，也就无从绕过它。

## 为什么默认带过期时间

一条两年前发出去、忘了收回的码，是长期后门。
`--expires-days` 默认 14 天：足够对方安排时间注册，
又不至于变成永久通道。
"""

import argparse
import asyncio
from datetime import timedelta

from sqlalchemy import text

from app.core.time_convention import utc_now
from app.database.session import AsyncSessionLocal
from app.services import invite_service


def format_code(invite) -> str:
    """给码分组显示，便于人工抄写。

    16 个字符连在一起很难核对（`ABCD2345EFGH6789`）。
    分组后（`ABCD-2345-EFGH-6789`）一眼能看出抄错了哪一段。

    注意：**这只是显示格式**，消费时会去掉连字符
    （见 `invite_service.normalize_code`）。
    """

    code = invite.code
    groups = [code[index : index + 4] for index in range(0, len(code), 4)]
    return "-".join(groups)


async def cmd_create(args) -> int:
    expires_at = None

    if args.expires_days is not None:
        if args.expires_days <= 0:
            print("  ✗ --expires-days 必须大于 0")
            return 1

        expires_at = utc_now() + timedelta(days=args.expires_days)

    async with AsyncSessionLocal() as db:
        codes = await invite_service.create_codes(
            db,
            count=args.count,
            note=args.note,
            expires_at=expires_at,
        )

    print()
    print("=" * 62)
    print(f"已签发 {len(codes)} 条邀请码")
    print("=" * 62)

    if args.note:
        print(f"  备注：{args.note}")

    print(
        f"  过期：{expires_at:%Y-%m-%d %H:%M}（UTC）"
        if expires_at
        else "  过期：永不过期 ⚠️ 建议用 --expires-days"
    )

    print()

    for invite in codes:
        print(f"    {format_code(invite)}")

    print()
    print("  ⚠️ 每条码只能用一次。发出去后无法收回（只能撤销未使用的）。")
    print(f"  ⚠️ 这批码最多带来 {len(codes)} 个账号 —— 因此成本可预算。")

    return 0


async def cmd_list(args) -> int:
    async with AsyncSessionLocal() as db:
        codes = await invite_service.list_codes(
            db,
            include_used=not args.unused,
        )

        # 用一次查询拿到使用者的用户名，避免逐条查
        used_ids = [
            item.used_by_user_id
            for item in codes
            if item.used_by_user_id is not None
        ]

        usernames: dict[int, str] = {}

        if used_ids:
            rows = (
                await db.execute(
                    text(
                        "SELECT id, username FROM users "
                        "WHERE id = ANY(:ids)"
                    ),
                    {"ids": used_ids},
                )
            ).all()

            usernames = {row[0]: row[1] for row in rows}

    if not codes:
        print("  没有邀请码。用 `create` 签发。")
        return 0

    now = utc_now()
    unused_count = 0

    print()
    print("=" * 78)
    print(f"邀请码（共 {len(codes)} 条）")
    print("=" * 78)
    print()
    print(f"  {'id':>5}  {'码':<22} {'状态':<10} {'使用者':<14} 备注")

    for invite in codes:
        if invite.used_by_user_id is not None:
            status = "已使用"
            who = usernames.get(invite.used_by_user_id, "?")
        elif invite.expires_at is not None and invite.expires_at <= now:
            status = "已过期"
            who = "—"
        else:
            status = "可用"
            who = "—"
            unused_count += 1

        note = (invite.note or "")[:20]

        print(
            f"  {invite.id:>5}  {format_code(invite):<22} "
            f"{status:<10} {who:<14} {note}"
        )

    print()
    print(f"  可用 {unused_count} 条 —— 即最多还能再注册 {unused_count} 个账号。")

    return 0


async def cmd_revoke(args) -> int:
    async with AsyncSessionLocal() as db:
        ok = await invite_service.revoke(db, code=args.code)

    if ok:
        print("  ✓ 已撤销")
        return 0

    print(
        "  ✗ 没有撤销：码不存在，或**已被使用**。\n"
        "    已使用的码不删 —— 它是注册记录的一部分，"
        "删掉就说不清那个账号是怎么来的。"
    )
    return 1


async def main() -> int:
    parser = argparse.ArgumentParser(
        description="邀请码管理（注册控制）"
    )
    sub = parser.add_subparsers(dest="command", required=True)

    create = sub.add_parser("create", help="签发邀请码")
    create.add_argument(
        "--count", type=int, default=1, help="签发数量（默认 1）"
    )
    create.add_argument(
        "--note", type=str, default=None, help="备注：给谁的"
    )
    create.add_argument(
        "--expires-days",
        type=int,
        default=14,
        help=(
            "有效天数（默认 14）。设为 0 表示永不过期 —— "
            "但那是长期后门，不建议"
        ),
    )
    create.set_defaults(handler=cmd_create)

    list_cmd = sub.add_parser("list", help="列出现有邀请码")
    list_cmd.add_argument(
        "--unused", action="store_true", help="只看还能用的"
    )
    list_cmd.set_defaults(handler=cmd_list)

    revoke = sub.add_parser("revoke", help="撤销未使用的邀请码")
    revoke.add_argument("code", help="邀请码（可带连字符）")
    revoke.set_defaults(handler=cmd_revoke)

    args = parser.parse_args()

    # `--expires-days 0` 表示永不过期
    if args.command == "create" and args.expires_days == 0:
        args.expires_days = None

    return await args.handler(args)


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
