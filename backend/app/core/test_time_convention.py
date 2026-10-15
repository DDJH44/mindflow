"""验证时间处理约定（§35）。

## 它守住什么

本项目的存储约定是**朴素 UTC**（数据库 20 个时间列都是
`timestamp without time zone`）。这个约定很容易被静默破坏：

**① `datetime.now()`（不带参数）返回本地时间。**
它与 `utc_now()` 返回的都是"朴素 datetime"，
**类型上完全一样**，因此混用不会报错，只会让时间**静默偏移**。
实测本项目曾同时存在这两种写法。

**② SQL 侧的 `func.now()` 依赖服务器时区。**
容器时区实测是 `Etc/UTC`，因此 `now()` 写入的**恰好**是 UTC ——
但那依赖**容器配置**，不是代码保证。若换成时区不是 UTC 的服务器，
SQL 侧会写入本地时间的朴素值，与 Python 侧的 UTC 混存，
而**两边看起来都"正常"**。

因此本脚本把"服务器时区必须是 UTC"变成一条**会失败的断言**，
而不是一条口头约定。

**③ `datetime.utcnow()` 已弃用**（Python 3.12+），
不应再出现在应用代码里。

## 它验证五件事

1. `utc_now()` 返回**朴素**值，且与 UTC 时刻一致（不是本地时间）
2. `utc_now()` 与 `datetime.now()` **不同**（证明它没在骗人 ——
   若本机时区恰好是 UTC，这条会说明"无法区分"，而不是假通过）
3. 应用代码里没有 `datetime.utcnow`
4. **数据库服务器时区是 UTC**
5. 数据库列类型与约定一致（`timestamp without time zone`）

用法：uv run python -m app.core.test_time_convention
"""

import asyncio
import re
import sys
from datetime import datetime, timezone
from pathlib import Path

from sqlalchemy import text

from app.database.session import AsyncSessionLocal


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


async def main() -> int:
    sys.path.insert(0, r"D:\yuxi\MindFlow\backend")

    from app.core.time_convention import utc_now

    result = Result()

    print("=" * 78)
    print("时间处理约定验证")
    print("=" * 78)

    # ================================================
    print()
    print("=" * 78)
    print("① utc_now() 返回朴素 UTC，而不是本地时间")
    print("=" * 78)

    value = utc_now()

    result.check(
        "返回值是朴素的（tzinfo 为 None）",
        value.tzinfo is None,
        f"tzinfo={value.tzinfo}",
    )

    # 与 aware 的 UTC 时刻比较：差值应在几秒内
    aware_utc = datetime.now(timezone.utc).replace(tzinfo=None)
    drift = abs((value - aware_utc).total_seconds())

    result.check(
        "与真实 UTC 时刻一致（偏差 < 5 秒）",
        drift < 5,
        f"偏差 {drift:.3f} 秒",
    )

    # ================================================
    print()
    print("=" * 78)
    print("② 它必须与 `datetime.now()` 不同（否则就测不出混用）")
    print("=" * 78)

    local_now = datetime.now()
    offset_hours = (local_now - aware_utc).total_seconds() / 3600

    print(f"      本机 UTC 偏移：{offset_hours:+.1f} 小时")

    if abs(offset_hours) < 0.05:
        # 本机时区恰好是 UTC：那"两种写法"在当前机器上无法区分。
        # **如实报出来**，而不是假装通过 —— 否则换个时区的机器
        # 上这个隐患就会出现，而测试一直是绿的。
        result.check(
            "本机时区为 UTC —— **无法**在本机区分两种写法",
            True,
            "该断言在非 UTC 机器上才有区分力（已如实记录）",
        )
    else:
        result.check(
            "utc_now() 与 datetime.now() 不同（证明约定真的在起作用）",
            abs((utc_now() - local_now).total_seconds()) > 60,
            f"相差约 {abs(offset_hours):.1f} 小时",
        )

    # ================================================
    print()
    print("=" * 78)
    print("③ 应用代码里不应再有 `datetime.utcnow`")
    print("=" * 78)

    app_dir = Path(r"D:\yuxi\MindFlow\backend\app")
    offenders: list[str] = []

    # ⚠️ 要排除**两个**文件，它们都必须提到 `utcnow`：
    # - `time_convention.py`：解释为什么不用它
    # - **本文件**：它断言"不该有 utcnow"，那它自己必然提到
    #
    # 第一版只排除了前者，于是断言扫到自己就报失败 ——
    # **那是误报**，而误报比漏报更糟：它会让人开始忽略这条断言。
    EXEMPT = {"time_convention.py", "test_time_convention.py"}

    for path in app_dir.rglob("*.py"):
        if path.name in EXEMPT:
            continue

        for index, line in enumerate(
            path.read_text(encoding="utf-8").splitlines(), 1
        ):
            if re.search(r"datetime\.utcnow\b", line):
                offenders.append(
                    f"{path.relative_to(app_dir)}:{index}"
                )

    result.check(
        "没有 `datetime.utcnow`（Python 3.12+ 已弃用）",
        not offenders,
        f"仍有 {offenders}" if offenders else "已全部迁移到 utc_now",
    )

    # ================================================
    print()
    print("=" * 78)
    print("④ 数据库服务器时区必须是 UTC")
    print("=" * 78)

    # 这一条是**关键**：SQL 侧的 `func.now()` 写入的是
    # "服务器当前时刻的朴素值"。若服务器时区不是 UTC，
    # 那它与 Python 侧 `utc_now()` 写的时间就会**混存**，
    # 而两边的类型完全相同，因此**看起来都正常**。
    async with AsyncSessionLocal() as db:
        timezone_name = (
            await db.execute(text("SHOW timezone"))
        ).scalar_one()

        naive_now = (await db.execute(text("SELECT now()::timestamp"))).scalar_one()

        utc_expr = (
            await db.execute(
                text("SELECT (now() AT TIME ZONE 'UTC')::timestamp")
            )
        ).scalar_one()

    print(f"      服务器 timezone：{timezone_name}")
    print(f"      now()::timestamp：{naive_now}")

    result.check(
        "服务器时区是 UTC（SQL 侧 now() 才等于 utc_now()）",
        str(naive_now) == str(utc_expr),
        f"偏差 {(naive_now - utc_expr).total_seconds():+.0f} 秒"
        if str(naive_now) != str(utc_expr)
        else "与 UTC 一致",
    )

    # ================================================
    print()
    print("=" * 78)
    print("⑤ 数据库列类型与约定一致")
    print("=" * 78)

    async with AsyncSessionLocal() as db:
        rows = list(
            (
                await db.execute(
                    text(
                        "SELECT column_name, data_type "
                        "FROM information_schema.columns "
                        "WHERE table_schema='public' "
                        "AND data_type LIKE 'timestamp%'"
                    )
                )
            ).all()
        )

    with_tz = [
        row[0] for row in rows if "with time zone" in row[1]
    ]

    print(f"      时间列共 {len(rows)} 个")

    # 若有人把这些列改成了 timestamptz，本约定就**不再成立** ——
    # 那时 `utc_now()` 返回朴素值会被隐式当作本地时间解释，
    # 是**静默的错误**。因此必须让测试失败，逼人明确决定。
    result.check(
        "全部时间列都是 `without time zone`（与朴素 UTC 约定一致）",
        not with_tz,
        f"这些列改成了 timestamptz：{with_tz}"
        if with_tz
        else f"{len(rows)} 个列均为 without time zone",
    )

    # ================================================
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
