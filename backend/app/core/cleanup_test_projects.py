"""清理由测试/测量脚本留下的项目与文档。

## 为什么需要它

实测发现：**界面上出现了 3 个名为"切块效率验证"的项目** ——
那是我写 N+1 测量脚本时早期版本留下的垃圾（当时的清理逻辑有 bug）。

这件事暴露了一个比"磁盘浪费"更严重的问题：
**测试垃圾会直接出现在用户的界面上。**
用户看到「三个同名项目、没有内容」，只会觉得这产品坏了 ——
而这与代码质量无关，纯粹是我污染了他的数据。

因此需要：

1. **一个可复用的清理工具**（不靠手敲 SQL）
2. **一个统一的测试数据命名约定**，让"哪些是垃圾"可判定

## 命名约定（新增，见 `TEST_PROJECT_PREFIX`）

所有测试/测量脚本创建的项目**必须**以
`TEST_PROJECT_PREFIX` 开头。本工具据此判定，不需要维护
一份"测试脚本名字清单" —— 那种清单迟早会漏。

历史遗留的名字（`切块效率验证` / `N+1 测量` / `延迟测量` 等）
也一并识别，因为它们已经在库里了。

## 安全设计

- **默认只列出、不删除**（`--purge` 才真删）
- 删除前打印**每个项目的内容摘要**（文档数、会话数），
  让人看清"要删的是什么"
- **拒绝删除有面试会话的项目**，除非显式 `--include-sessions`
  —— 一场真实面试比一个测试项目值钱得多，
  而"名字像测试"不足以证明它没有真实数据

用法：
    uv run python -m app.core.cleanup_test_projects
    uv run python -m app.core.cleanup_test_projects --purge
"""

import argparse
import asyncio
import sys

from sqlalchemy import text

from app.database.session import AsyncSessionLocal

# 新增的测试项目前缀约定。
#
# 从今天起，测试/测量脚本创建的项目**必须**用它开头。
# 这样"哪些是垃圾"变成可判定的 —— 而不是靠记住
# "我写过哪些脚本"。
TEST_PROJECT_PREFIX = "__test__"

# 历史遗留的测试项目名（在本约定之前就已经存在于库里）。
#
# 保留这份名单是**刻意的妥协**：它们已经在数据里了，
# 不识别就清不掉。但它不会增长 —— 新脚本一律用前缀。
LEGACY_TEST_NAMES = (
    "切块效率验证",
    "N+1 测量",
    "延迟测量",
)

# E2E 套件创建的项目名前缀（`test_e2e_http` 用）
E2E_PROJECT_PATTERN = "E2E 项目%"


async def collect() -> list[dict]:
    """列出候选的测试项目，附带内容摘要。"""

    async with AsyncSessionLocal() as db:
        rows = list(
            (
                await db.execute(
                    text(
                        """
                        SELECT
                            p.id,
                            p.name,
                            u.username,
                            p.created_at,
                            (SELECT count(*) FROM documents d
                             WHERE d.project_id = p.id) AS documents,
                            (SELECT count(*) FROM document_chunks c
                             JOIN documents d ON d.id = c.document_id
                             WHERE d.project_id = p.id) AS chunks,
                            (SELECT count(*) FROM interview_sessions s
                             WHERE s.project_id = p.id) AS sessions
                        FROM projects p
                        JOIN users u ON u.id = p.owner_id
                        WHERE p.name LIKE :prefix
                           OR p.name = ANY(:legacy)
                           OR p.name LIKE :e2e
                        ORDER BY p.id
                        """
                    ),
                    {
                        "prefix": f"{TEST_PROJECT_PREFIX}%",
                        "legacy": list(LEGACY_TEST_NAMES),
                        "e2e": E2E_PROJECT_PATTERN,
                    },
                )
            ).all()
        )

    return [
        {
            "id": row[0],
            "name": row[1],
            "owner": row[2],
            "created_at": row[3],
            "documents": row[4],
            "chunks": row[5],
            "sessions": row[6],
        }
        for row in rows
    ]


async def purge(
    projects: list[dict],
    include_sessions: bool,
) -> int:
    """删除给定的项目及其文档、文本块。返回删除数量。

    ⚠️ 不删向量库里的向量。
    那些会让 `check_orphan_vectors` 报出来，再用它自己的
    `--purge` 清理 —— 让每件事只有一个负责的工具。
    """

    # 安全闸：有面试会话的项目默认不删。
    if include_sessions:
        targets = projects
    else:
        targets = [item for item in projects if item["sessions"] == 0]

        skipped = [item for item in projects if item["sessions"] > 0]

        if skipped:
            print()
            print("  跳过（含面试会话，加 --include-sessions 才会删）：")

            for item in skipped:
                print(
                    f"    id={item['id']} {item['name']} "
                    f"（{item['sessions']} 场会话）"
                )

    if not targets:
        return 0

    ids = [item["id"] for item in targets]

    async with AsyncSessionLocal() as db:
        # 顺序：chunks → documents → sessions（若有）→ project
        await db.execute(
            text(
                """
                DELETE FROM document_chunks
                WHERE document_id IN (
                    SELECT id FROM documents WHERE project_id = ANY(:ids)
                )
                """
            ),
            {"ids": ids},
        )

        await db.execute(
            text("DELETE FROM documents WHERE project_id = ANY(:ids)"),
            {"ids": ids},
        )

        if include_sessions:
            await db.execute(
                text(
                    "DELETE FROM interview_sessions "
                    "WHERE project_id = ANY(:ids)"
                ),
                {"ids": ids},
            )

        deleted = await db.execute(
            text("DELETE FROM projects WHERE id = ANY(:ids)"),
            {"ids": ids},
        )

        await db.commit()

    return deleted.rowcount or 0


async def main() -> int:
    sys.path.insert(0, r"D:\yuxi\MindFlow\backend")

    parser = argparse.ArgumentParser(description="清理测试项目")
    parser.add_argument(
        "--purge",
        action="store_true",
        help="真的删除（默认只列出）",
    )
    parser.add_argument(
        "--include-sessions",
        action="store_true",
        help="连有面试会话的项目一起删（危险）",
    )

    args = parser.parse_args()

    print("=" * 76)
    print("测试项目清理")
    print("=" * 76)

    projects = await collect()

    if not projects:
        print()
        print("  ✓ 没有发现测试项目")
        return 0

    print()
    print(f"  发现 {len(projects)} 个候选：")
    print()
    print(
        f"  {'id':>5}  {'名称':<26} {'归属':<14} "
        f"{'文档':>4} {'块':>5} {'会话':>4}"
    )

    for item in projects:
        print(
            f"  {item['id']:>5}  {item['name'][:26]:<26} "
            f"{item['owner'][:14]:<14} "
            f"{item['documents']:>4} {item['chunks']:>5} "
            f"{item['sessions']:>4}"
        )

    if not args.purge:
        print()
        print("  （默认只列出。要真的删除请加 --purge）")
        return 0

    print()
    deleted = await purge(projects, args.include_sessions)

    print()
    print(f"  已删除 {deleted} 个项目")
    print()
    print(
        "  提示：它们的向量可能还在 Milvus 里 —— "
        "跑 `uv run python -m app.core.check_orphan_vectors --purge` 清理"
    )

    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
