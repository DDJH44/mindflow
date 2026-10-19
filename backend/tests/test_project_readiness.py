"""验证资料体检：面试能用上哪些资料。

## 它防的是什么

面试只检索**四类**材料（简历 / 岗位要求 / 项目材料 / 代码），
且只检索**已索引**的。这两点用户都看不到，于是产生两种典型误解：

1. **"面试怎么老问简历"** —— 实际是只有简历已索引，
   其余三类没传或没索引成功。
2. **"我明明传了资料，为什么说没有"** —— 实际是那份资料
   被标成了「其他」，而「其他」**不参与面试出题**
   （它索引成功、列表里显示"已索引·可检索"，所以看起来完全正常）。

实测：有项目各有一份已索引的 `other` 资料，体检却报
"还没有任何已索引的资料" —— 若不解释，用户会以为这是 bug。

## 它验证

- 只统计四类，且只把 `embedded` 算作"已索引"
- `other` 单独计数，并写进 `advice`（不参与出题这件事必须说出来）
- 没有可用资料时 `usable=False`

用法：uv run python -m tests.test_project_readiness
"""

import asyncio
import sys

from sqlalchemy import text

from app.database.session import AsyncSessionLocal

TEST_PROJECT_NAME = "__test__readiness"


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


async def cleanup() -> None:
    async with AsyncSessionLocal() as db:
        await db.execute(
            text("DELETE FROM projects WHERE name = :n"),
            {"n": TEST_PROJECT_NAME},
        )
        await db.commit()


async def seed() -> dict[str, int]:
    """建一个项目：resume 已索引、code 未索引、other 已索引。"""

    async with AsyncSessionLocal() as db:
        user_id = (
            await db.execute(
                text("SELECT id FROM users WHERE username=:u"),
                {"u": "mindflow"},
            )
        ).scalar_one()

        project_id = (
            await db.execute(
                text(
                    "INSERT INTO projects (name, owner_id, status, "
                    "created_at, updated_at) "
                    "VALUES (:n, :u, 'active', now(), now()) "
                    "RETURNING id"
                ),
                {"n": TEST_PROJECT_NAME, "u": user_id},
            )
        ).scalar_one()

        for document_type, status in (
            ("resume", "embedded"),
            ("resume", "embedded"),
            ("code", "chunked"),
            ("other", "embedded"),
            ("other", "pending"),
        ):
            await db.execute(
                text(
                    "INSERT INTO documents ("
                    "name, original_filename, file_type, project_id, "
                    "document_type, status, content, "
                    "created_at, updated_at) "
                    "VALUES (:n, :n, 'txt', :p, :t, :s, 'x', "
                    "now(), now())"
                ),
                {
                    "n": f"{document_type}-{status}.txt",
                    "p": project_id,
                    "t": document_type,
                    "s": status,
                },
            )

        await db.commit()

    return {"project_id": project_id, "user_id": user_id}


async def main() -> int:
    sys.path.insert(0, r"D:\yuxi\MindFlow\backend")

    from app.models.user import User
    from app.routers.projects import get_project_readiness

    result = Result()

    print("=" * 78)
    print("资料体验证")
    print("=" * 78)

    await cleanup()

    try:
        ids = await seed()

        async with AsyncSessionLocal() as db:
            user = (
                await db.execute(
                    text("SELECT id FROM users WHERE id=:i"),
                    {"i": ids["user_id"]},
                )
            ).scalar_one()

            # 直接调端点函数（不走 HTTP）：它已经做了鉴权与取数，
            # 这里要验的是**统计口径**，不是路由装配。
            body = await get_project_readiness(
                project_id=ids["project_id"],
                db=db,
                current_user=User(id=user, username="mindflow"),
            )

        # ================================================
        print()
        print("=" * 78)
        print("① 只统计四类，且只把 embedded 算作已索引")
        print("=" * 78)

        by_type = {
            item["type"]: item for item in body["types"]
        }

        print(
            "      "
            + "  ".join(
                f"{item['label']}:{item['indexed']}/{item['total']}"
                for item in body["types"]
            )
        )

        result.check(
            "resume 2 份且都已索引",
            by_type["resume"]["indexed"] == 2
            and by_type["resume"]["total"] == 2,
            f"{by_type['resume']['indexed']}/{by_type['resume']['total']}",
        )
        result.check(
            "code 有 1 份但未索引（chunked 不算已索引）",
            by_type["code"]["indexed"] == 0
            and by_type["code"]["total"] == 1,
            f"{by_type['code']['indexed']}/{by_type['code']['total']}",
        )
        result.check(
            "已索引类型只含「简历」",
            body["indexed_types"] == ["简历"],
            f"{body['indexed_types']}",
        )
        result.check(
            "缺失类型含「代码」",
            "代码" in body["missing_types"],
            f"{body['missing_types']}",
        )

        # ================================================
        print()
        print("=" * 78)
        print("② 「其他」单独计数，并且必须解释它不参与出题")
        print("=" * 78)

        # 这是本端点最容易被漏掉的一半：`other` 索引成功、
        # 列表里显示"已索引·可检索"，但面试永远检索不到它。
        result.check(
            "other 只有 1 份已索引（pending 的不算）",
            body["other_indexed"] == 1,
            f"{body['other_indexed']}",
        )
        result.check(
            "other 总数 2 份",
            body["other_total"] == 2,
            f"{body['other_total']}",
        )
        result.check(
            "advice 说明了「其他」不参与面试出题",
            "其他" in body["advice"] and "不参与" in body["advice"],
            body["advice"][:60],
        )
        result.check(
            "advice 说明了面试只用哪四类",
            "简历" in body["advice"]
            and "代码" in body["advice"],
        )

        # ================================================
        print()
        print("=" * 78)
        print("③ 可用性与建议")
        print("=" * 78)

        result.check(
            "有已索引资料 → usable True",
            body["usable"] is True,
        )
        result.check(
            "advice 指出了只有简历可用",
            "只有" in body["advice"] and "简历" in body["advice"],
            body["advice"][:40],
        )

        # 只有 other 的项目：usable 必须是 False ——
        # 否则用户会以为"可以出好题了"。
        async with AsyncSessionLocal() as db:
            only_other_id = (
                await db.execute(
                    text(
                        "INSERT INTO projects (name, owner_id, status, "
                        "created_at, updated_at) "
                        "VALUES (:n, :u, 'active', now(), now()) "
                        "RETURNING id"
                    ),
                    {
                        "n": TEST_PROJECT_NAME + "-only-other",
                        "u": ids["user_id"],
                    },
                )
            ).scalar_one()

            await db.execute(
                text(
                    "INSERT INTO documents ("
                    "name, original_filename, file_type, project_id, "
                    "document_type, status, content, "
                    "created_at, updated_at) "
                    "VALUES ('only-other.txt', 'only-other.txt', 'txt', "
                    ":p, 'other', 'embedded', 'x', now(), now())"
                ),
                {"p": only_other_id},
            )
            await db.commit()

        async with AsyncSessionLocal() as db:
            only_other = await get_project_readiness(
                project_id=only_other_id,
                db=db,
                current_user=User(
                    id=ids["user_id"], username="mindflow"
                ),
            )

        result.check(
            "只有「其他」资料 → usable False（面试用不上）",
            only_other["usable"] is False,
            f"{only_other['indexed_types']}",
        )
        result.check(
            "只有「其他」时 advice 明确说只能出通用题",
            "通用题" in only_other["advice"],
            only_other["advice"][:50],
        )

        async with AsyncSessionLocal() as db:
            await db.execute(
                text("DELETE FROM projects WHERE id = :i"),
                {"i": only_other_id},
            )
            await db.commit()

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
