"""验证 doctor 真的能检测出故障。

**一个永远报 OK 的健康检查比没有更糟** —— 它给人虚假的安全感，
还会让人在真实故障时继续怀疑代码。

因此这个套件做两件事：
1. 全部依赖指向**不可达地址**，断言四项都报 FAIL
2. 依赖正常时，断言 doctor 退出码为 0（不误报）

第 2 项容易漏，但同样重要：健康检查误报会让人去修不存在的问题。

用法：uv run python -m app.core.test_doctor
"""

import asyncio
import contextlib
import io
import sys

from sqlalchemy.ext.asyncio import (
    async_sessionmaker,
    create_async_engine,
)

from app.core import doctor
from app.core.config import settings

results: list[tuple[str, bool]] = []


def record(label: str, ok: bool) -> None:
    results.append((label, ok))
    print(f"  [{'OK  ' if ok else 'FAIL'}] {label}")


# "依赖是否可用"这件事只由这些检查决定。
#
# 刻意排除 `向量一致性`：它反映的是**数据状态**（孤儿向量），
# 而不是依赖是否可用。库里有孤儿向量时它就会报 FAIL，
# 那与"补丁有没有还原"无关 —— 混在一起断言会让本套件
# 的结论随数据状态漂移，看起来像代码问题。
SERVICE_CHECKS = {
    "PostgreSQL",
    "数据库迁移",
    "Milvus",
    "LLM",
    "Embedding",
}


async def run_checks_silently(
    include_external: bool = True,
) -> list[tuple[str, str, str]]:
    """跑完所有检查，吞掉打印，返回结果。

    `include_external` 控制是否检查 LLM / Embedding。
    回归门禁里必须传 False —— 否则门禁里这一步会**真实调用模型**，
    既慢又让门禁依赖上游可用性（那正是门禁要避免的）。
    """

    doctor.results.clear()

    buffer = io.StringIO()

    with contextlib.redirect_stdout(buffer):
        await doctor.check_database()
        await doctor.check_migrations()
        await doctor.check_redis()
        await doctor.check_milvus()

        if include_external:
            await doctor.check_llm()
            await doctor.check_embedding()

    return list(doctor.results)


async def main() -> int:
    sys.path.insert(0, r"D:\yuxi\MindFlow\backend")

    # ---------------- 1. 依赖正常时不误报 ----------------
    print()
    print("=" * 74)
    print("1. 依赖正常时不误报")
    print("=" * 74)

    healthy = await run_checks_silently(include_external=False)
    healthy_failures = [
        name
        for name, status, _detail in healthy
        if status == doctor.FAIL and name in SERVICE_CHECKS
    ]

    record(
        "依赖正常时没有 FAIL",
        healthy_failures == [],
    )
    print(f"      检查项: {[n for n, _s, _d in healthy]}")
    print(f"      服务类失败项: {healthy_failures}")

    # 顺带确认 doctor **能**检出一致性问题（它不该被静默忽略）
    consistency = [
        (name, status)
        for name, status, _detail in healthy
        if name == "向量一致性"
    ]
    record(
        "向量一致性被纳入检查（有孤儿向量时应报出）",
        bool(consistency),
        )
    if consistency:
        print(f"      向量一致性当前状态: {consistency[0][1]}")

    # ---------------- 2. 依赖不可达时必须报 FAIL ----------------
    print()
    print("=" * 74)
    print("2. 依赖不可达时全部报 FAIL")
    print("=" * 74)

    broken_url = "postgresql+asyncpg://x:x@127.0.0.1:9/none"

    saved = {
        "database_url": settings.database_url,
        "llm_api_key": settings.llm_api_key,
        "llm_base_url": settings.llm_base_url,
        "embedding_api_key": settings.embedding_api_key,
        "embedding_base_url": settings.embedding_base_url,
        "AsyncSessionLocal": doctor.AsyncSessionLocal,
    }

    import app.services.milvus_vector_store as milvus_module

    original_milvus_init = milvus_module.MilvusVectorStore.__init__

    try:
        settings.database_url = broken_url
        settings.llm_api_key = "sk-broken-on-purpose"
        settings.llm_base_url = "http://127.0.0.1:9/v1"
        settings.embedding_api_key = "sk-broken-on-purpose"
        settings.embedding_base_url = "http://127.0.0.1:9/v1"

        # 数据库引擎在导入时就绑定了，必须重建
        broken_engine = create_async_engine(broken_url)
        doctor.AsyncSessionLocal = async_sessionmaker(
            bind=broken_engine, expire_on_commit=False
        )

        # Milvus 的地址是构造函数默认值，也要打补丁
        def broken_init(
            self,
            uri="http://127.0.0.1:9",
            token="root:Milvus",
        ):
            original_milvus_init(self, uri=uri, token=token)

        milvus_module.MilvusVectorStore.__init__ = broken_init

        broken = await run_checks_silently()

    finally:
        settings.database_url = saved["database_url"]
        settings.llm_api_key = saved["llm_api_key"]
        settings.llm_base_url = saved["llm_base_url"]
        settings.embedding_api_key = saved["embedding_api_key"]
        settings.embedding_base_url = saved["embedding_base_url"]
        doctor.AsyncSessionLocal = saved["AsyncSessionLocal"]
        milvus_module.MilvusVectorStore.__init__ = original_milvus_init

    detected = {
        name
        for name, status, _detail in broken
        if status == doctor.FAIL
    }

    # 这四项是"环境问题看起来像代码回归"的主要来源，必须都能检出
    expected = {"PostgreSQL", "Milvus", "LLM", "Embedding"}
    missing = expected - detected

    record(
        "四项外部依赖都能检出故障",
        not missing,
    )
    print(f"      检出: {sorted(detected)}")
    if missing:
        print(f"      漏报: {sorted(missing)}")

    # ---------------- 3. 修好之后不残留假故障 ----------------
    print()
    print("=" * 74)
    print("3. 恢复配置后重新检查")
    print("=" * 74)

    restored = await run_checks_silently(include_external=False)
    restored_failures = [
        name
        for name, status, _detail in restored
        if status == doctor.FAIL and name in SERVICE_CHECKS
    ]

    record(
        "恢复后没有服务类 FAIL（说明补丁被正确还原）",
        restored_failures == [],
    )
    if restored_failures:
        print(f"      仍失败: {restored_failures}")

    print()
    print("=" * 74)
    passed = sum(1 for _, ok in results if ok)
    failed = [label for label, ok in results if not ok]
    print(f"通过 {passed} / {len(results)}")
    if failed:
        print("失败项:")
        for label in failed:
            print("  -", label)
        return 1

    print("全部通过")
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
