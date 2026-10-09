"""清理本轮验证产生的测试用户（含其项目、会话与文档）。

刻意**不硬编码表名** —— 先前猜 `answers` 表猜错了。
改为从 pg_tables 读取实际表名，再按 user_id / owner_id
依赖数据库自身的级联删除。

用法：uv run python -m app.core.cleanup_test_users
"""

import asyncio
import sys

from sqlalchemy import text

from app.database.session import AsyncSessionLocal

# 本轮探针与端到端测试使用的用户名前缀
PREFIXES = (
    "e2e_",
    "probe_",
    "probe2_",
    "probe3_",
    "probe4_",
    "probe5_",
    "probe6_",
    "probe7_",
)

# **绝不删除**的用户。这些是真实用户，不是测试数据。
#
# 加这个白名单的原因：清理脚本按前缀匹配，而"看起来像测试前缀"
# 与"确实是测试数据"不是一回事 —— `e2e_` 这种前缀真实用户也可能用。
# 曾有一次清理把用户 `不开挖机` 的项目连带删掉（脚本按 owner_id
# 级联删项目），所以这里显式保护。
PROTECTED = (
    "mindflow",
    "不开挖机",
)


async def main() -> int:
    async with AsyncSessionLocal() as db:
        tables = set(
            (
                await db.execute(
                    text(
                        "SELECT tablename FROM pg_tables "
                        "WHERE schemaname = 'public'"
                    )
                )
            ).scalars().all()
        )

        users = list(
            (
                await db.execute(
                    text("SELECT id, username FROM users ORDER BY id")
                )
            ).all()
        )

        test_users = [
            row
            for row in users
            if any((row[1] or "").startswith(p) for p in PREFIXES)
            and (row[1] or "") not in PROTECTED
        ]
        keep = [row for row in users if row not in test_users]

        print(f"  用户总数: {len(users)}")
        print(f"  测试用户: {len(test_users)}")
        print(f"  保留: {keep}")

        protected_present = [
            row for row in users if (row[1] or "") in PROTECTED
        ]
        if protected_present:
            print(f"  受保护（绝不删除）: {protected_present}")

        if not test_users:
            print("  无需清理")
            return 0

        ids = [row[0] for row in test_users]

        for user_id in ids:
            # 项目所属的文档（向量已由 DELETE 端点或本脚本单独处理）
            project_ids = list(
                (
                    await db.execute(
                        text(
                            "SELECT id FROM projects "
                            "WHERE owner_id = :u"
                        ),
                        {"u": user_id},
                    )
                ).scalars().all()
            )

            document_ids = []
            if project_ids and "documents" in tables:
                document_ids = list(
                    (
                        await db.execute(
                            text(
                                "SELECT id FROM documents "
                                "WHERE project_id = ANY(:p)"
                            ),
                            {"p": project_ids},
                        )
                    ).scalars().all()
                )

            # 会话
            if "interview_sessions" in tables:
                session_ids = list(
                    (
                        await db.execute(
                            text(
                                "SELECT id FROM interview_sessions "
                                "WHERE user_id = :u"
                            ),
                            {"u": user_id},
                        )
                    ).scalars().all()
                )

                # 轨迹（若表存在）
                if session_ids and "interview_status_history" in tables:
                    await db.execute(
                        text(
                            "DELETE FROM interview_status_history "
                            "WHERE session_id = ANY(:s)"
                        ),
                        {"s": session_ids},
                    )

                # 题目（答案表若存在则先删）
                if session_ids:
                    question_ids = []
                    if "interview_questions" in tables:
                        question_ids = list(
                            (
                                await db.execute(
                                    text(
                                        "SELECT id FROM interview_questions "
                                        "WHERE session_id = ANY(:s)"
                                    ),
                                    {"s": session_ids},
                                )
                            ).scalars().all()
                        )

                    if question_ids:
                        for table in tables:
                            if "answer" in table:
                                await db.execute(
                                    text(
                                        f"DELETE FROM {table} "
                                        "WHERE question_id = ANY(:q)"
                                    ),
                                    {"q": question_ids},
                                )

                        await db.execute(
                            text(
                                "DELETE FROM interview_questions "
                                "WHERE session_id = ANY(:s)"
                            ),
                            {"s": session_ids},
                        )

                    for table in tables:
                        if "evaluation" in table:
                            await db.execute(
                                text(
                                    f"DELETE FROM {table} "
                                    "WHERE session_id = ANY(:s)"
                                ),
                                {"s": session_ids},
                            )

                await db.execute(
                    text(
                        "DELETE FROM interview_sessions "
                        "WHERE user_id = :u"
                    ),
                    {"u": user_id},
                )

            if "interview_usage" in tables:
                await db.execute(
                    text(
                        "DELETE FROM interview_usage "
                        "WHERE user_id = :u"
                    ),
                    {"u": user_id},
                )

            # 文档（chunk 由外键级联）
            if document_ids and "documents" in tables:
                await db.execute(
                    text("DELETE FROM documents WHERE id = ANY(:d)"),
                    {"d": document_ids},
                )

            if project_ids:
                await db.execute(
                    text("DELETE FROM projects WHERE id = ANY(:p)"),
                    {"p": project_ids},
                )

            await db.execute(
                text("DELETE FROM users WHERE id = :u"),
                {"u": user_id},
            )

        await db.commit()

        remaining = list(
            (
                await db.execute(
                    text("SELECT id, username FROM users ORDER BY id")
                )
            ).all()
        )

    print(f"  清理后剩余用户: {remaining}")

    # 收尾：清掉因此产生的孤儿向量。
    #
    # 必须放在这里，而不是指望使用者记得手动跑：本脚本是**直接删库**，
    # 绕过了 `DELETE /projects/{id}` 端点，因此 Milvus 里的向量不会被
    # 连带删除。实测每清一个测试用户就留下若干孤儿向量，
    # 它们会占用 top-k 名额、静默降低召回（D52）。
    purged = await purge_orphan_vectors()
    if purged:
        print(f"  已清理孤儿向量 {purged} 个")

    return 0


async def purge_orphan_vectors() -> int:
    """删除 Milvus 中已无对应 chunk 的向量。

    返回清理数量。导入放在函数内，避免本脚本在无 Milvus 环境下
    连"列出用户"都跑不了。
    """

    try:
        from app.core.check_orphan_vectors import (
            collect_orphans,
        )
        from app.services.milvus_vector_store import MilvusVectorStore

        store = MilvusVectorStore()
        orphans, _ = await collect_orphans(store)

        if not orphans:
            return 0

        await store.delete(ids=sorted(orphans))
        return len(orphans)

    except Exception as exc:  # noqa: BLE001
        print(
            f"  孤儿向量清理失败（{type(exc).__name__}）：{exc}"
        )
        print("  请手动运行：uv run python -m app.core.check_orphan_vectors --purge")
        return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
