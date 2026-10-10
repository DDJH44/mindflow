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
import re
import subprocess
import sys
import time

# 全部验证套件。
#
# `needs_llm` 标记该套件会真实调用模型 —— `--offline` 时跳过，
# 因为离线回归的目标是"快速确认没破坏逻辑"。
#
# ⚠️ 这个标记必须与实际相符。标错会让离线回归**变慢且脆弱**：
# 未标记的套件照样调模型，于是"离线"仍要等几十秒、
# 且会因端点抖动而失败。实测误标了两个套件，
# 它们各自耗时 80s 以上。
SUITES: tuple[tuple[str, bool], ...] = (
    # 纯逻辑 / 只依赖数据库，不调模型
    ("test_doctor", False),
    ("test_logging", False),
    ("test_document_parsing", False),
    ("test_interview_list", False),
    ("test_ability_profile", False),
    ("test_usage_quota", False),
    ("test_document_upload", False),
    ("test_resume_upload", False),
    # 会调用模型
    ("test_question_quota", True),
    ("test_question_budget", True),
    ("test_interview_api", True),
    ("test_interview_flow", True),
    # 这两个也调模型：前者经 `/start` 生成首题，
    # 后者经 `finish_interview` 触发整场评价。
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


def run_suite(name: str) -> tuple[str, str, float]:
    """跑一个套件，返回 (状态, 说明, 耗时秒)。"""

    started = time.monotonic()

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

    selected = [
        (name, needs_llm)
        for name, needs_llm in SUITES
        if not args.filter or args.filter in name
    ]

    if args.offline:
        selected = [
            (name, needs_llm)
            for name, needs_llm in selected
            if not needs_llm
        ]

    print("=" * 74)
    print(f"回归门禁：{len(selected)} 个套件")
    if args.offline:
        print("（离线模式：已跳过需要调用模型的套件）")
    print("=" * 74)
    print()

    outcomes: list[tuple[str, str, str, float]] = []

    for name, _needs_llm in selected:
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
