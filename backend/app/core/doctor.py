"""依赖健康检查：一眼看出"环境问题"还是"代码问题"。

**为什么需要它**：现有 `/api/health` 只返回配置里写的服务名与环境，
**不检查任何依赖** —— 后端返回 200 并不代表它能用。

实测踩过三次坑，每次都要花时间才能分清是哪一类：

| 现象 | 真实原因 |
| --- | --- |
| 套件 exit=1、无摘要 | LLM 端点瞬时超时（不是代码回归） |
| 连接被拒 | Docker 没起（不是代码问题） |
| 检索为空 | Milvus 一致性（D53） |

本工具把每项依赖**单独探一遍**并给出结论，这样失败时
第一眼就能定位，不必逐个排除。

用法：

    # 基础检查（数据库 / 迁移 / 向量库状态），不调外部模型
    uv run python -m app.core.doctor

    # 加上外部模型连通性（会真实调用，产生少量费用）
    uv run python -m app.core.doctor --llm
"""

import argparse
import asyncio
import sys
import time

from sqlalchemy import text

from app.core.config import settings
from app.database.session import AsyncSessionLocal

# 每项检查的结论
OK = "OK"
FAIL = "FAIL"
WARN = "WARN"

results: list[tuple[str, str, str]] = []


def report(name: str, status: str, detail: str = "") -> None:
    results.append((name, status, detail))

    icon = {OK: "✓", FAIL: "✗", WARN: "!"}[status]
    line = f"  [{icon}] {name}"

    if detail:
        line += f" — {detail}"

    print(line)


async def check_database() -> None:
    """PostgreSQL 连通性 + 版本。"""

    try:
        started = time.monotonic()

        async with AsyncSessionLocal() as db:
            version = (
                await db.execute(text("SELECT version()"))
            ).scalar_one()
            tables = (
                await db.execute(
                    text(
                        "SELECT COUNT(*) FROM pg_tables "
                        "WHERE schemaname = 'public'"
                    )
                )
            ).scalar_one()

        elapsed = (time.monotonic() - started) * 1000

        report(
            "PostgreSQL",
            OK,
            f"{tables} 张表，{elapsed:.0f}ms（{version.split(',')[0]}）",
        )

    except Exception as exc:  # noqa: BLE001
        report(
            "PostgreSQL",
            FAIL,
            f"{type(exc).__name__}: {str(exc)[:80]}"
            "（Docker 起了吗？docker start mindflow-postgres）",
        )


async def check_migrations() -> None:
    """数据库的迁移版本是否为最新。

    为什么值得检查：模型加了字段但迁移没跑，报错会是
    "column does not exist" —— 那看起来像代码 bug，
    实际只是少跑一次 `alembic upgrade head`。
    """

    try:
        from pathlib import Path

        from alembic.config import Config
        from alembic.script import ScriptDirectory

        backend_dir = Path(__file__).resolve().parents[2]

        config = Config(str(backend_dir / "alembic.ini"))
        config.set_main_option(
            "script_location", str(backend_dir / "alembic")
        )

        script = ScriptDirectory.from_config(config)
        head = script.get_current_head()

        async with AsyncSessionLocal() as db:
            current = (
                await db.execute(
                    text("SELECT version_num FROM alembic_version")
                )
            ).scalar_one_or_none()

        if current == head:
            report("数据库迁移", OK, f"已在 head（{head}）")
        else:
            report(
                "数据库迁移",
                FAIL,
                f"当前 {current}，最新 {head}"
                "（跑 uv run alembic upgrade head）",
            )

    except Exception as exc:  # noqa: BLE001
        report(
            "数据库迁移",
            WARN,
            f"无法确认：{type(exc).__name__}: {str(exc)[:70]}",
        )


async def check_redis() -> None:
    """Redis 配置是否存在。

    ⚠️ 这里**只检查配置，不声称它在被使用**。

    实测：`redis_url` 只出现在 `config.py` 与测试文本里，
    业务代码从不使用它。因此"Redis 连得上"并不代表什么 ——
    报 OK 会让人以为缓存已经在工作。

    这个警告本身有价值：它让"启动了一堆用不上的容器"
    这件事可见，而不是靠人记得。
    """

    if not settings.redis_url:
        report("Redis", WARN, "未配置 redis_url")
        return

    report(
        "Redis",
        WARN,
        "已配置，但**业务代码尚未使用**"
        "（检索与缓存都还没接入）—— 连得上不代表在用",
    )


async def check_milvus(with_vectors: bool = True) -> None:
    """Milvus 连通性 + 集合状态 + 一致性级别。"""

    try:
        from app.services.milvus_vector_store import MilvusVectorStore

        store = MilvusVectorStore()
        collection = MilvusVectorStore.COLLECTION_NAME

        if not store.client.has_collection(
            collection_name=collection
        ):
            report(
                "Milvus",
                WARN,
                f"连得上，但集合 {collection} 还不存在"
                "（首次上传资料时会自动创建）",
            )
            return

        description = store.client.describe_collection(
            collection_name=collection
        )
        level = description.get("consistency_level_name", "未知")

        # 一致性级别必须是 Strong —— 否则"上传成功但检索不到"（D53）
        if level == "Strong":
            report("Milvus", OK, f"集合 {collection}，一致性 {level}")
        else:
            # 自动收紧而不是只报错。
            #
            # `create_collection` 只在集合不存在时执行，因此早期用
            # Bounded 建出来的老集合永远修不到 —— 而老库恰恰是
            # 问题正在发生的地方。这里顺手改成 Strong（幂等）。
            changed = store.ensure_strong_consistency()

            if changed:
                report(
                    "Milvus",
                    WARN,
                    f"集合一致性原为 {level}，已自动改为 Strong"
                    "（否则插入后立刻检索可能落空，见 D53）",
                )
            else:
                report(
                    "Milvus",
                    FAIL,
                    f"集合一致性为 {level}，应为 Strong，"
                    "且自动修改失败 —— 请检查 Milvus 权限",
                )

        if not with_vectors:
            return

        # 向量与 chunk 的一致性
        from app.core.check_orphan_vectors import collect_orphans

        orphans, scanned = await collect_orphans(store)

        if orphans:
            report(
                "向量一致性",
                FAIL,
                f"发现 {len(orphans)} 个孤儿向量"
                "（占用 top-k 名额、静默降低召回）—— "
                "跑 uv run python -m app.core.check_orphan_vectors --purge",
            )
        else:
            report(
                "向量一致性",
                OK,
                f"扫描 {len(scanned)} 个向量，无孤儿",
            )

    except Exception as exc:  # noqa: BLE001
        report(
            "Milvus",
            FAIL,
            f"{type(exc).__name__}: {str(exc)[:80]}"
            "（docker start mindflow-etcd mindflow-minio mindflow-milvus）",
        )


async def check_llm() -> None:
    """LLM 端点端到端可用性。

    只做一次极短的调用。**不能只发一个未认证请求看 401** ——
    那只证明网络通，不证明 key 有效、模型名对、能返回内容。
    """

    if not settings.llm_api_key or not settings.llm_model:
        report("LLM", WARN, "未配置 llm_api_key / llm_model")
        return

    try:
        from app.services.openai_llm_service import OpenAILLMService

        started = time.monotonic()

        answer = await OpenAILLMService().generate(
            prompt="只回答两个字：正常",
            temperature=0.0,
            max_tokens=10,
            # 健康检查不重试：目的是**快速判定可用性**。
            # 默认 4 次尝试 + 指数退避在不可达端点上要耗 45 秒，
            # 那会让"检查环境"本身变成一件慢事 —— 人就不愿跑它了。
            max_attempts=1,
        )

        elapsed = time.monotonic() - started

        report(
            "LLM",
            OK,
            f"{settings.llm_model} 响应正常，{elapsed:.1f}s"
            f"（返回 {answer.strip()[:20]!r}）",
        )

    except Exception as exc:  # noqa: BLE001
        # 这是**最常被误认为代码回归**的失败
        report(
            "LLM",
            FAIL,
            f"{type(exc).__name__}: {str(exc)[:80]}"
            " —— 这是上游问题，不是代码问题；稍后重试",
        )


async def check_embedding() -> None:
    """嵌入服务可用性（检索依赖它）。"""

    if not settings.embedding_api_key:
        report("Embedding", WARN, "未配置 embedding_api_key")
        return

    try:
        from app.services.openai_embedding_service import (
            OpenAIEmbeddingService,
        )

        started = time.monotonic()

        vectors = await OpenAIEmbeddingService().embed_texts(
            ["健康检查"]
        )

        elapsed = time.monotonic() - started
        dimension = len(vectors[0]) if vectors else 0

        expected = 1024

        if dimension == expected:
            report(
                "Embedding",
                OK,
                f"{settings.embedding_model}，{dimension} 维，"
                f"{elapsed:.1f}s",
            )
        else:
            # 维度不对会让 Milvus 插入直接失败，值得单独指出
            report(
                "Embedding",
                FAIL,
                f"返回 {dimension} 维，但集合要求 {expected} 维",
            )

    except Exception as exc:  # noqa: BLE001
        report(
            "Embedding",
            FAIL,
            f"{type(exc).__name__}: {str(exc)[:80]}",
        )


async def main() -> int:
    parser = argparse.ArgumentParser(
        description="依赖健康检查",
    )
    parser.add_argument(
        "--llm",
        action="store_true",
        help="同时检查 LLM 与 Embedding（会真实调用，产生少量费用）",
    )
    args = parser.parse_args()

    print("=" * 74)
    print("MindFlow 依赖健康检查")
    print("=" * 74)

    await check_database()
    await check_migrations()
    await check_redis()
    await check_milvus()

    if args.llm:
        await check_llm()
        await check_embedding()
    else:
        print()
        print("  （未检查 LLM / Embedding；加 --llm 会真实调用一次）")

    # ---------------- 汇总 ----------------
    failures = [item for item in results if item[1] == FAIL]
    warnings = [item for item in results if item[1] == WARN]

    print()
    print("=" * 74)
    print(
        f"结果：{len(results) - len(failures) - len(warnings)} 项正常、"
        f"{len(warnings)} 项提示、{len(failures)} 项失败"
    )

    if failures:
        print()
        print("失败项：")
        for name, _status, detail in failures:
            print(f"  - {name}: {detail}")
        print()
        print(
            "  ⚠️ 上面这些是**环境问题**，不是代码缺陷。"
            "在排除它们之前，测试失败不能作为代码问题的证据。"
        )

    if warnings:
        print()
        print("提示项：")
        for name, _status, detail in warnings:
            print(f"  - {name}: {detail}")

    print()

    # 基础依赖失败才返回非零；提示不阻断
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
