"""回归门禁：一次跑完全部验证套件并给出统一结论。

**为什么需要它**：目前有 13 个验证套件，逐个跑既慢又容易漏；
而"这次改动到底破坏了什么"必须有一个**单一的、可信的**答案。

**一个重要设计：区分"代码失败"与"环境不可用"。**
实测反复出现两类误判：

| 现象 | 真实原因 |
| --- | --- |
| 套件以非零码退出、无摘要 | LLM 端点瞬时超时（不是代码回归）|
| 大量套件同时失败 | Docker 没起（不是代码问题）|

因此本门禁使用**三态**，而不是简单的"过了/没过"：

- `PASS` —— 套件全部断言通过
- `SKIP` —— 套件明确报告"依赖不可用，未执行"（退出码 2）
- `FAIL` —— 真正的断言失败

并且**先把依赖健康检查跑一遍**（`doctor`），依赖有问题时
直接告诉调用方"先修环境"，而不是让 13 个套件各报一次连接被拒。

用法：

    # 全部（会真实调用模型，耗时长）
    uv run python -m app.core.test_all

    # 跳过需要模型的套件（快速回归，约几十秒）
    uv run python -m app.core.test_all --offline

    # 只跑名字匹配的套件
    uv run python -m app.core.test_all --filter interview
"""

import argparse
import asyncio
import os
import re
import subprocess
import sys
import time

# 全部验证套件。
#
# 第二列 `needs_embedding`：该套件需要**可用的嵌入端点**才能跑。
# 嵌入不可用时（额度耗尽、端点变更），它们会以各种方式失败
# （"最终状态=failed"、"开始面试返回 503"）——
# 那些失败**不是代码回归**，而是前提缺失。
#
# 实测：嵌入额度耗尽时，三个套件共报 11 处失败，
# 而真正的原因是同一个。若不区分，门禁会持续误报
# 直到端点恢复，人就学会了忽略它。
SUITES: tuple[tuple[str, bool], ...] = (
    # (套件名, 是否需要嵌入端点)
    ("test_doctor", False),
    ("test_logging", False),
    ("test_embedding_batching", False),
    ("check_eval_dataset", False),
    ("test_async_indexing", True),
    ("test_document_parsing", False),
    ("test_interview_list", False),
    ("test_ability_profile", False),
    ("test_usage_quota", True),
    ("test_quota_refund", False),
    ("test_knowledge_extraction", True),
    # 上传体积上限：四条防线分别在请求头、读取流、字符数、块数。
    # 必须走**真实 HTTP**，否则会绕过第一层（头部早退）。
    ("test_upload_limits", True),
    # 限流：注册按 IP、上传按用户。断言里包含一条**设计选择**
    # （Redis 不可用时必须拒绝，不能静默放行）。
    ("test_rate_limit", False),
    # 启动守卫与 CORS：占位密钥在生产环境必须**拒绝启动**；
    # 运行时不得允许任意来源（读配置看不出"*"挂错位置）。
    ("test_secret_and_cors", False),
    # dotenv 隔离（D69）：`pymilvus` 导入时会 `load_dotenv()`
    # 把 `.env` 注入 `os.environ`，而环境变量**优先级高于** `.env`
    # → 曾导致子进程用旧密钥签发令牌、一批套件报 401。
    ("test_dotenv_isolation", False),
    # 时间约定（§35）：存储是**朴素 UTC**。断言应用代码无已弃用的
    # `utcnow`、服务器时区是 UTC、20 个时间列都是 `without time zone`
    # —— 最后一条会在有人改成 `timestamptz` 时失败，
    # 逼人明确决定（那时 `utc_now()` 的朴素值会被隐式当本地时间解释）。
    ("test_time_convention", False),
    # 连接失效恢复（§36）：用 `pg_terminate_backend` **真的**踢掉连接，
    # 验证"检测 → 重建池子 → 重试一次"这条链路，
    # 并断言业务异常**不会**被误判为连接故障（不吞真缺陷）。
    ("test_db_recovery", False),
    # 切块写入效率：断言查询次数不随块数线性增长（防 N+1 回归），
    # 且返回值（id / created_at）仍可用**且读取不触发查询**。
    ("test_chunk_insert_efficiency", False),
    # 评估报告保真度（D70）：报告里的 chunk_size / chunk_overlap /
    # top_k 必须与**实际执行**的参数一致。实测曾错记 overlap=100
    # 而实际是 0 —— 判据取自实际切块产出，不是"常量等于常量"。
    ("test_eval_report_fidelity", False),
    ("test_document_upload", True),
    ("test_resume_upload", True),
    # 会调用模型的套件
    ("test_question_quota", True),
    ("test_question_budget", True),
    ("test_interview_api", True),
    ("test_interview_flow", True),
    ("test_status_history", True),
    ("test_paused_finish", True),
    ("test_e2e_http", True),
)

# 需要额外起一个"坏 LLM"后端的套件，默认跳过并说明。
# 让门禁强行依赖第二个后端只会让人不愿跑它。
MANUAL_SUITES: tuple[tuple[str, str], ...] = (
    (
        "test_llm_error_mapping",
        "需要 8099 端口上一个指向不可达 LLM 的后端"
        "（uv run python -m app.core.upload_with_broken_chunker 的变体）",
    ),
    (
        "test_chunk_guard",
        "需要 8098 端口上一个切块器被打坏的后端"
        "（uv run python -m app.core.upload_with_broken_chunker）",
    ),
    (
        "test_waiting_ui",
        "需要**慢响应**环境：uv run python -m app.core.slow_backend（8001）"
        " + cd web && VITE_BACKEND_ORIGIN=http://127.0.0.1:8001 "
        "npx vite --port 5174。"
        "它必须测慢路径 —— 快响应下等待提示根本不显示",
    ),
    (
        "ui_walkthrough",
        "需要前端 :5173 + 后端 :8000。"
        "断言包含 D58 回归（历史页每行都有可点动作）、"
        "D55 回归（accept 不承诺后端不支持的类型）"
        "与 D56 回归（暂停会话能恢复）。"
        "⚠️ 嵌入端点不可用时，面试页与暂停恢复的断言会被"
        "明确跳过并在输出里标注「结论不完整」",
    ),
    (
        "check_computed_fonts",
        "需要前端 :5173 + 后端 :8000 + chromium。"
        "在**真实浏览器**里读 getComputedStyle，验证排版令牌"
        "在浏览器里的实际计算值 —— CSS 变量未定义会**静默回退**、"
        "更具体的选择器会**静默覆盖**，两者都不报错，"
        "因此「源码看起来对」不能作为结论",
    ),
)

PASS = "PASS"
SKIP = "SKIP"
FAIL = "FAIL"


def extract_counts(output: str) -> str:
    """从套件输出里取"通过 N / M"。"""

    matches = re.findall(
        r"通过\s+(\d+)\s*/\s*(\d+)", output
    )

    if not matches:
        return ""

    passed, total = matches[-1]
    return f"{passed}/{total}"


def reset_rate_limits() -> None:
    """清掉 Redis 里的限流计数。**只给门禁在跑套件前调用。**

    ## 为什么需要它

    限流是**安全控制**，而套件测的是**行为**。实测多个套件累计上传
    超过"30 次/小时"的限额，于是套件报 429，失败信息是
    「操作过于频繁」—— 完全指向被测代码之外。

    ## 为什么由门禁统一清，而不是让每个套件自己清

    让套件各自清会产生**隐藏的清理依赖**：清少了互相消耗、
    清多了误删真实计数，而且每个新套件都要记得这件事。

    ## 为什么不靠"测试模式"关掉限流

    我第一版加了个 `TESTING` 环境变量来关限流，但它**不生效** ——
    因为限流判断发生在**运行中的后端进程**里，而后端是用
    `start-all.ps1` 启动的（没设那个变量）。
    从测试进程设环境变量只能影响**测试自己**，
    影响不到另一个进程里的判断。

    这是个值得记住的教训：**先问清"是谁在做这个判断"**，
    再决定在哪里改。

    ## 为什么不干脆调高限额

    那会削弱生产默认值。而"测试要跑几十次上传"本身是
    测试的特性，不是生产的特性 —— 不该为它放宽线上配置。

    Redis 不可用时**静默跳过**：限流本身 fail-closed，
    而门禁不该因为清理失败就中断（套件会自己报出真实问题）。
    """

    try:
        from app.core.rate_limit import get_redis

        async def _clear() -> int:
            client = get_redis()
            removed = 0

            async for key in client.scan_iter(match="ratelimit:*"):
                await client.delete(key)
                removed += 1

            await client.aclose()
            return removed

        asyncio.run(_clear())

    except Exception:  # noqa: BLE001
        # 清理失败不拦停门禁 —— 套件会自己暴露真实问题
        pass


def run_suite(name: str) -> tuple[str, str, float]:
    """跑一个套件，返回 (状态, 说明, 耗时秒)。"""

    started = time.monotonic()

    # 每个套件前清一次限流计数 —— 避免套件之间互相消耗额度。
    #
    # 放在这里（而不是每个套件内部）是因为：限流判断发生在
    # **运行中的后端**里，因此只能在"发请求之前"清，
    # 而门禁是唯一知道"什么时候要开始一个新套件"的地方。
    reset_rate_limits()

    process = subprocess.run(
        [sys.executable, "-m", f"app.core.{name}"],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )

    elapsed = time.monotonic() - started
    output = (process.stdout or "") + (process.stderr or "")
    code = process.returncode

    if code == 0:
        return PASS, extract_counts(output), elapsed

    if code == 2:
        # 套件自己判定为"依赖不可用、未执行"
        return SKIP, "依赖不可用，未执行", elapsed

    # 抓第一条失败项，便于一眼定位
    failure = ""

    for line in output.splitlines():
        if "[FAIL]" in line:
            failure = line.split("[FAIL]")[-1].strip()
            break

    if not failure:
        # 没有 FAIL 行说明是异常退出（崩溃）
        for line in reversed(output.splitlines()):
            if "Error" in line or "Traceback" in line:
                failure = line.strip()
                break

    reason = extract_counts(output) or failure or f"退出码 {code}"

    return FAIL, reason, elapsed


async def check_embedding_available() -> tuple[bool, str]:
    """探测嵌入端点是否可用。返回 (可用, 说明)。

    只做一次极短的调用。判据是"能不能拿到向量"，
    而不是"端点是不是活着" —— 额度耗尽时端点活着但会拒绝。

    **为什么必须单独探测**：嵌入与 LLM 是两个端点、两套额度。
    嵌入耗尽时 LLM 仍然可用，因此只看 LLM 无法发现这个问题。
    """

    from app.core.config import settings

    if not settings.embedding_api_key:
        return False, "未配置 embedding_api_key"

    try:
        from app.services.openai_embedding_service import (
            OpenAIEmbeddingService,
        )

        vectors = await OpenAIEmbeddingService().embed_texts(["探测"])

    except Exception as exc:  # noqa: BLE001
        message = str(exc)

        # 额度耗尽是常见且**可读**的原因，单独点出来，
        # 避免只看到一句 BadRequestError。
        if "quota" in message.lower() or "FreeTier" in message:
            return False, (
                f"额度耗尽（{settings.embedding_model}）："
                "需充值或在控制台关闭「仅用免费额度」"
            )

        return False, f"不可用：{type(exc).__name__}: {message[:80]}"

    if not vectors:
        return False, "端点返回空结果"

    return True, (
        f"可用（{settings.embedding_model}，{len(vectors[0])} 维）"
    )


async def check_environment() -> bool:
    """先跑依赖健康检查。返回**是否可以继续跑套件**。

    ⚠️ 判据只针对**服务是否可用**，不针对数据一致性。

    为什么：孤儿向量这类问题会让套件的**结论变弱**
    （召回被静默降低），但不会让 13 个套件各报一次失败 ——
    它不该拦停门禁。若把它当"环境不可用"，门禁会频繁无法运行，
    而人一旦习惯"门禁跑不起来"，它就失去意义了。

    真正该拦停的是"连不上"：那种情况下套件的失败
    完全不能作为代码问题的证据。
    """

    print("=" * 74)
    print("步骤 0：依赖健康检查")
    print("=" * 74)

    from app.core import doctor

    original = doctor.results
    doctor.results = []

    await doctor.check_database()
    await doctor.check_migrations()
    await doctor.check_redis()
    await doctor.check_milvus()

    outcomes = list(doctor.results)
    doctor.results = original

    # 服务不可用 → 拦停
    service_failures = [
        item
        for item in outcomes
        if item[1] == doctor.FAIL and item[0] != "向量一致性"
    ]

    # 数据一致性问题 → 只是提示
    data_warnings = [
        item
        for item in outcomes
        if item[1] == doctor.FAIL and item[0] == "向量一致性"
    ]

    if service_failures:
        print()
        print("  ✗ 基础依赖不可用，后续套件的失败**不能**作为代码问题的证据：")
        for name, _status, detail in service_failures:
            print(f"    - {name}: {detail}")
        print()
        print("  先修环境，再跑门禁。")
        return False

    # HTTP 服务可达性。
    #
    # 与依赖检查分开：依赖（库 / 向量库 / 模型）能连，
    # 不代表**应用与前端**在跑。实测后端停掉时，
    # 5 个套件各自报一次 `httpx.ConnectError` ——
    # 5 条失败信息指向 5 个"不同的套件坏了"，
    # 而真因只有一个：忘了起服务。
    #
    # 那种噪音会让门禁的结论失去意义，因此这里也拦停。
    from app.core import check_services

    print()
    print("=" * 74)
    print("步骤 0.1：HTTP 服务可达性")
    print("=" * 74)

    # **两个都探测**，不用 `and` 短路 ——
    # 短路会让"两个都不可达"只报出第一个。
    backend_ok = check_services.probe(
        "后端 :8000",
        check_services.BACKEND_HEALTH,
        "启动：uv run uvicorn app.main:app --host 127.0.0.1 "
        "--port 8000 --no-access-log",
    )

    proxy_ok = check_services.probe(
        "前端代理 :5173",
        check_services.PROXY_HEALTH,
        "启动：cd web && npx vite --port 5173",
    )

    services_ok = backend_ok and proxy_ok

    if not services_ok:
        print()
        print(
            "  ✗ 有服务不可达。**在解决它之前，套件的失败不能"
            "作为代码问题的证据** —— 多个套件会各报一次连接失败，"
            "而真因只有一个。"
        )
        return False

    if data_warnings:
        print()
        print("  ⚠️ 数据一致性有问题（会削弱结论，但不拦停）：")
        for name, _status, detail in data_warnings:
            print(f"    - {name}: {detail}")

    print()
    print("  ✓ 基础依赖正常")
    return True


def main() -> int:
    parser = argparse.ArgumentParser(
        description="回归门禁：跑完全部验证套件",
    )
    parser.add_argument(
        "--offline",
        action="store_true",
        help="跳过需要调用模型的套件（快速回归）",
    )
    parser.add_argument(
        "--filter",
        default=None,
        help="只跑名字包含该子串的套件",
    )
    parser.add_argument(
        "--skip-env-check",
        action="store_true",
        help="跳过依赖健康检查（不推荐）",
    )
    args = parser.parse_args()

    if not args.skip_env_check:
        if not asyncio.run(check_environment()):
            return 1
        print()

    # 嵌入端点可用性。
    #
    # 与 LLM 分开探测：嵌入额度耗尽时 LLM 仍然可用，
    # 而依赖真实嵌入的套件会以各种方式失败
    # （"最终状态=failed"、"开始面试返回 503"）。
    # 实测三个套件共报 11 处失败，真因却是同一个。
    embedding_ok, embedding_detail = asyncio.run(
        check_embedding_available()
    )

    print("=" * 74)
    print("步骤 0.2：嵌入端点可用性")
    print("=" * 74)
    print(
        f"   [{'✓' if embedding_ok else '✗'}] {embedding_detail}"
    )
    print()

    selected = [
        (name, needs_embedding)
        for name, needs_embedding in SUITES
        if not args.filter or args.filter in name
    ]

    if args.offline:
        # 离线模式的本意是"不调外部服务"，因此嵌入类套件一并跳过。
        selected = [
            (name, needs_embedding)
            for name, needs_embedding in selected
            if not needs_embedding
        ]
    elif not embedding_ok:
        # 嵌入不可用：跳过依赖它的套件，**而不是让它们失败**。
        #
        # 这不是"放过失败"：那些套件测的是额度逻辑与上传链路，
        # 它们没有坏，是前提缺失。把它们报成失败会让门禁
        # 持续误报直到端点恢复 —— 而人一旦习惯忽略门禁，
        # 它就不再有任何作用。
        selected = [
            (name, needs_embedding)
            for name, needs_embedding in selected
            if not needs_embedding
        ]

    print("=" * 74)
    print(f"回归门禁：{len(selected)} 个套件")
    if args.offline:
        print("（离线模式：已跳过需要外部服务的套件）")
    elif not embedding_ok:
        print(
            "（嵌入端点不可用：已跳过依赖它的套件 —— "
            "那些失败会是前提缺失，不是代码回归）"
        )
    print("=" * 74)
    print()

    outcomes: list[tuple[str, str, str, float]] = []

    for name, _needs_embedding in selected:
        status, detail, elapsed = run_suite(name)
        outcomes.append((name, status, detail, elapsed))

        icon = {PASS: "✓", SKIP: "~", FAIL: "✗"}[status]
        print(
            f"  [{icon}] {name:<26} {status:<4} "
            f"{detail:<22} {elapsed:5.1f}s"
        )

    passed = [item for item in outcomes if item[1] == PASS]
    skipped = [item for item in outcomes if item[1] == SKIP]
    failed = [item for item in outcomes if item[1] == FAIL]

    total_time = sum(item[3] for item in outcomes)

    print()
    print("=" * 74)
    print(
        f"结果：{len(passed)} 通过、{len(skipped)} 跳过、"
        f"{len(failed)} 失败（共 {total_time:.0f}s）"
    )

    if skipped:
        print()
        print("跳过（依赖不可用，不是代码问题）：")
        for name, _status, detail, _elapsed in skipped:
            print(f"  - {name}: {detail}")

    if failed:
        print()
        print("失败：")
        for name, _status, detail, _elapsed in failed:
            print(f"  - {name}: {detail}")

    if MANUAL_SUITES and not args.filter:
        print()
        print("需手动准备环境的套件（未纳入本次运行）：")
        for name, reason in MANUAL_SUITES:
            print(f"  - {name}: {reason}")

    print()

    if failed:
        print("门禁未通过")
        return 1

    if skipped and not args.offline:
        # 有套件因依赖不可用而跳过：不能声称"全绿"，
        # 但也不该判为失败 —— 那是环境问题。
        print(
            "门禁通过（但有套件因依赖不可用被跳过，"
            "结论不完整）"
        )
        return 0

    print("门禁通过")
    return 0


if __name__ == "__main__":
    sys.exit(main())
