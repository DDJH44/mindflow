"""对比两份面试评测报告，让"换模型"变成一件便宜的操作。

为什么需要它：
评测脚本本来就是可重复运行的，但**没有基线对比**，
每次换模型后跑完只能人工翻两份 JSON 判断"有没有变差" ——
于是重跑的成本不是 5 分钟，而是 5 分钟加一堆主观判断。
结果是没人愿意重跑，只能攒着"以后再测"。

有了本工具，换模型的标准流程是：

    1. 跑 `cli.py`（约 5 分钟，24 次 LLM 调用）
    2. 跑 `report_compare.py`（几秒，**不调 LLM**）
    3. 看结论

纯读取，不发起任何 LLM 调用、不访问数据库。
"""

import argparse
import glob
import json
import os
import sys
from pathlib import Path

# 报告目录：与本文件同级的上级目录的 evaluation_reports
REPORTS_DIR = (
    Path(__file__).resolve().parents[3] / "evaluation_reports"
)

# 基线指针文件。内容是一份报告的文件名 ——
# 用指针而不是"最新的那份"：基线应该是**显式选定**的，
# 否则每次跑完评测都会把上一次的结果变成基线，无法跨模型比较。
BASELINE_POINTER = REPORTS_DIR / "BASELINE"

# 会影响解读的配置项。任一不同，分数就不可直接比较。
COMPARABILITY_KEYS = (
    "llm_model",
    "llm_base_url",
    "analyzer_temperature",
    "follow_up_temperature",
)


def load_report(path: str | os.PathLike) -> dict:
    """读取一份评测报告。"""

    with open(path, encoding="utf-8") as handle:
        report = json.load(handle)

    report["_path"] = str(path)

    return report


def turn_key(case_id: str, turn_index) -> str:
    """轮次的唯一键：同一个 case 可能有多轮。"""

    return f"{case_id}#{turn_index}"


def index_turns(report: dict) -> dict:
    """把报告摊平成 {轮次键: 轮次数据}。"""

    indexed = {}

    for case in report.get("cases", []):
        case_id = case["case_id"]

        for turn in case.get("turns", []):
            indexed[
                turn_key(case_id, turn.get("turn_index"))
            ] = turn

    return indexed


def tier_of(turn: dict, dimension: str) -> str | None:
    """取某维度的档位字母。

    报告的 `analysis.anchors` 结构是
    `{"quality": {"anchor": "B", ...}, "depth": ..., "completeness": ...}` ——
    不是以维度名直接映射到字母，取错会静默拿到 None。
    """

    anchors = (turn.get("analysis") or {}).get("anchors") or {}
    entry = anchors.get(dimension)

    if isinstance(entry, dict):
        return entry.get("anchor")

    return entry


def score_of(turn: dict, field: str):
    """取某维度的派生分数。"""

    return (turn.get("analysis") or {}).get(field)


DIMENSIONS = (
    ("quality", "answer_quality"),
    ("depth", "technical_depth"),
    ("completeness", "completeness"),
)


def print_header(
    baseline: dict,
    candidate: dict,
) -> list[str]:
    """打印两份报告的口径，并返回不可比的项。"""

    base_cfg = baseline.get("run_config", {})
    cand_cfg = candidate.get("run_config", {})

    print("=" * 78)
    print("报告口径")
    print("=" * 78)
    print(f"  基线: {os.path.basename(baseline['_path'])}")
    print(f"  候选: {os.path.basename(candidate['_path'])}")
    print()

    differing = []

    for key in COMPARABILITY_KEYS:
        base_value = base_cfg.get(key)
        cand_value = cand_cfg.get(key)
        mark = "  " if base_value == cand_value else "≠ "
        print(f"  {mark}{key:<26} {base_value}  →  {cand_value}")
        if base_value != cand_value:
            differing.append(key)

    base_sampling = base_cfg.get("sampling") or {}
    cand_sampling = cand_cfg.get("sampling") or {}
    for key in (
        "offline_analysis_samples",
        "offline_evaluation_samples",
    ):
        base_value = base_sampling.get(key)
        cand_value = cand_sampling.get(key)
        mark = "  " if base_value == cand_value else "≠ "
        print(f"  {mark}{key:<26} {base_value}  →  {cand_value}")
        if base_value != cand_value:
            differing.append(key)

    if differing:
        print()
        print(
            "  ⚠️ 以下配置不同，**分数不可直接比较**："
            + "、".join(differing)
        )
        print(
            "     这与 ADR-021 处理线上/离线口径分离是同一条原则。"
        )

    return differing


def print_summary(
    baseline: dict,
    candidate: dict,
) -> dict:
    """打印汇总指标差异。"""

    base = baseline.get("summary", {})
    cand = candidate.get("summary", {})

    print()
    print("=" * 78)
    print("汇总指标")
    print("=" * 78)

    rows = [
        (
            "通过的 case",
            f"{base.get('passed_case_count')}/{base.get('case_count')}",
            f"{cand.get('passed_case_count')}/{cand.get('case_count')}",
        ),
        (
            "通过的轮次",
            f"{base.get('turn_pass_count')}/{base.get('turn_count')}",
            f"{cand.get('turn_pass_count')}/{cand.get('turn_count')}",
        ),
        (
            "出错轮次",
            base.get("errored_turn_count"),
            cand.get("errored_turn_count"),
        ),
    ]

    base_rules = base.get("rule_pass_rate") or {}
    cand_rules = cand.get("rule_pass_rate") or {}
    for rule in sorted(set(base_rules) | set(cand_rules)):
        rows.append(
            (
                f"  规则 {rule}",
                f"{base_rules.get(rule, float('nan')):.4f}",
                f"{cand_rules.get(rule, float('nan')):.4f}",
            )
        )

    for label, key in (
        ("R1 平均命中率", "r1_average_hit_rate"),
        ("R3 平均命中率", "r3_average_hit_rate"),
    ):
        base_value = base.get(key)
        cand_value = cand.get(key)
        rows.append(
            (
                label,
                (
                    f"{base_value:.4f}"
                    if isinstance(base_value, (int, float))
                    else base_value
                ),
                (
                    f"{cand_value:.4f}"
                    if isinstance(cand_value, (int, float))
                    else cand_value
                ),
            )
        )

    base_latency = (base.get("latency_ms") or {}).get("average")
    cand_latency = (cand.get("latency_ms") or {}).get("average")
    rows.append(
        (
            "平均延迟(ms)",
            (
                f"{base_latency:.0f}"
                if isinstance(base_latency, (int, float))
                else base_latency
            ),
            (
                f"{cand_latency:.0f}"
                if isinstance(cand_latency, (int, float))
                else cand_latency
            ),
        )
    )

    print(f"  {'指标':<32} {'基线':<14} 候选")
    for label, base_value, cand_value in rows:
        flag = ""
        if (
            isinstance(base_value, str)
            and isinstance(cand_value, str)
            and base_value != cand_value
        ):
            flag = "  ← 变化"
        print(
            f"  {label:<32} {str(base_value):<14} "
            f"{cand_value}{flag}"
        )

    return {
        "base_rules": base_rules,
        "cand_rules": cand_rules,
        "base_errors": base.get("errored_turn_count") or 0,
        "cand_errors": cand.get("errored_turn_count") or 0,
        "base_latency": base_latency,
        "cand_latency": cand_latency,
        "base_r1": base.get("r1_average_hit_rate"),
        "cand_r1": cand.get("r1_average_hit_rate"),
        "base_r3": base.get("r3_average_hit_rate"),
        "cand_r3": cand.get("r3_average_hit_rate"),
    }


def print_turns(
    baseline: dict,
    candidate: dict,
    scores_comparable: bool,
) -> list[str]:
    """逐轮对比档位与分数，返回发生变化的轮次标签。"""

    base_turns = index_turns(baseline)
    cand_turns = index_turns(candidate)

    only_base = sorted(set(base_turns) - set(cand_turns))
    only_cand = sorted(set(cand_turns) - set(base_turns))

    print()
    print("=" * 78)
    print("逐轮对比（档位 / 分数）")
    print("=" * 78)

    if only_base or only_cand:
        print(
            f"  ⚠️ 轮次集合不同：仅基线有 {only_base}，"
            f"仅候选有 {only_cand}"
        )
        print(
            "     这通常说明数据集改过。数据集变更会让对比失效 ——"
            "      请确认是有意为之（§21 维护规则第 7 条）。"
        )
        print()

    changed = []

    for key in sorted(set(base_turns) & set(cand_turns)):
        base_turn = base_turns[key]
        cand_turn = cand_turns[key]

        parts = []
        turn_changed = False

        for dimension, field in DIMENSIONS:
            base_tier = tier_of(base_turn, dimension)
            cand_tier = tier_of(cand_turn, dimension)
            base_score = score_of(base_turn, field)
            cand_score = score_of(cand_turn, field)

            if base_tier != cand_tier:
                turn_changed = True
                parts.append(
                    f"{dimension}: {base_tier}({base_score})"
                    f" → {cand_tier}({cand_score})  ← 档位变化"
                )
            else:
                parts.append(
                    f"{dimension}: {base_tier}({base_score})"
                )

        base_passed = base_turn.get("passed")
        cand_passed = cand_turn.get("passed")
        if base_passed != cand_passed:
            turn_changed = True
            parts.append(
                f"通过: {base_passed} → {cand_passed}  ← 判定变化"
            )

        marker = "≠ " if turn_changed else "  "
        print(f"  {marker}{key}")
        for part in parts:
            print(f"      {part}")

        if turn_changed:
            changed.append(key)

    if not changed:
        print()
        print("  所有轮次的档位与判定**完全一致**。")
    elif not scores_comparable:
        print()
        print(
            "  ⚠️ 口径不同，上述档位变化**不能**解读为"
            "模型变好或变差（见 ADR-021）。"
        )

    return changed


def print_rule_failures(baseline: dict, candidate: dict) -> None:
    """列出候选报告中未通过的具体规则，便于定位。"""

    print()
    print("=" * 78)
    print("候选报告里未通过的轮次")
    print("=" * 78)

    found = False

    for case in candidate.get("cases", []):
        for turn in case.get("turns", []):
            failed_rules = [
                name
                for name, rule in (turn.get("rules") or {}).items()
                if not rule.get("passed")
                and not rule.get("deprecated")
            ]

            if turn.get("status") != "success" or failed_rules:
                found = True
                label = turn_key(
                    case["case_id"], turn.get("turn_index")
                )
                print(f"  {label}")
                if turn.get("status") != "success":
                    print(
                        f"      状态: {turn.get('status')} "
                        f"{turn.get('error_type') or ''}"
                    )
                for name in failed_rules:
                    rule = turn["rules"][name]
                    print(
                        f"      {name}: "
                        f"missed={rule.get('missed')}"
                    )

    if not found:
        print("  无（全部通过且无出错轮次）")


def print_verdict(
    baseline: dict,
    candidate: dict,
    summary: dict,
    differing: list[str],
    changed_turns: list[str],
) -> int:
    """给出结论与退出码。"""

    print()
    print("=" * 78)
    print("结论")
    print("=" * 78)

    exit_code = 0

    # 1. 出错轮次：硬失败。与 §15"不得悄悄忽略失败 case"一致。
    if summary["cand_errors"] > 0:
        print(
            f"  ✗ 候选报告有 {summary['cand_errors']} 个出错轮次 ——"
            "先修稳定性再比分数"
        )
        exit_code = 2
    elif summary["cand_errors"] > summary["base_errors"]:
        print(
            f"  ✗ 出错轮次增加：{summary['base_errors']} → "
            f"{summary['cand_errors']}"
        )
        exit_code = 2

    # 2. 规则通过率：倒退要报出来
    regressed = []
    for rule in sorted(set(summary["base_rules"]) | set(summary["cand_rules"])):
        base_rate = summary["base_rules"].get(rule, 0)
        cand_rate = summary["cand_rules"].get(rule, 0)
        if cand_rate < base_rate:
            regressed.append((rule, base_rate, cand_rate))

    if regressed:
        for rule, base_rate, cand_rate in regressed:
            print(
                f"  ✗ 规则倒退 {rule}: "
                f"{base_rate:.4f} → {cand_rate:.4f}"
            )
        if exit_code == 0:
            exit_code = 1
    elif not summary["cand_errors"]:
        print("  ✓ 规则通过率未倒退")

    # 3. 命中率：给出数值但不作为门禁（有采样噪声）
    print()
    print("  命中率变化（**仅供参考**，有采样噪声，不作为门禁）：")
    for label, base_value, cand_value in (
        ("R1", summary["base_r1"], summary["cand_r1"]),
        ("R3", summary["base_r3"], summary["cand_r3"]),
    ):
        if isinstance(base_value, (int, float)) and isinstance(
            cand_value, (int, float)
        ):
            delta = cand_value - base_value
            arrow = "↑" if delta > 0 else ("↓" if delta < 0 else "=")
            print(
                f"    {label}: {base_value:.4f} → "
                f"{cand_value:.4f}  {arrow}{abs(delta):.4f}"
            )
        else:
            print(f"    {label}: {base_value} → {cand_value}")

    # 4. 延迟
    if isinstance(summary["base_latency"], (int, float)) and isinstance(
        summary["cand_latency"], (int, float)
    ):
        delta = summary["cand_latency"] - summary["base_latency"]
        ratio = (
            summary["cand_latency"] / summary["base_latency"]
            if summary["base_latency"]
            else 0
        )
        print(
            f"    延迟: {summary['base_latency']:.0f}ms → "
            f"{summary['cand_latency']:.0f}ms  "
            f"（{ratio:.2f}×，{delta:+.0f}ms）"
        )

    # 5. 档位变化
    print()
    if changed_turns:
        print(
            f"  档位/判定发生变化的轮次（{len(changed_turns)} 个）: "
            f"{changed_turns}"
        )
        if differing:
            print(
                "     口径不同，**不能**解读为模型变好或变差 ——"
                "只说明「换了模型，分数分布变了」，这是预期内的。"
            )
        else:
            print(
                "     口径相同，这个变化值得看一眼是不是"
                "评分行为真的变了。"
            )
    else:
        print("  档位/判定无变化。")

    # 6. 与分数方差的关系
    print()
    print(
        "  提醒：单次评测的分数本身就有波动（本项目实测同一输入"
        "10 次重复仍有约 2 轮超阈值，§24.10）。"
    )
    print(
        "  因此**不要**用一两轮的档位变化判定模型优劣；"
        "规则通过率与出错轮次才是可用的门禁（ADR-015）。"
    )

    return exit_code


def resolve_baseline(
    candidate_path: str | None,
    reports_dir: Path | None = None,
) -> tuple[str, str]:
    """确定要比的两份报告。

    返回 (基线路径, 候选路径)。

    `reports_dir` 可注入：自测用它指向临时目录，
    而不是改模块常量 —— 改常量会被函数读到的旧名字绕过。
    """

    folder = reports_dir or REPORTS_DIR
    pointer = folder / "BASELINE"

    files = sorted(
        glob.glob(str(folder / "interview_evaluation_*.json"))
    )

    if len(files) < 2:
        raise SystemExit(
            f"评测报告不足两份（当前 {len(files)} 份），"
            f"无法对比。先跑 `python -m app.evaluation.interview.cli`。"
        )

    # 基线：优先取指针文件
    baseline_path = None

    if pointer.exists():
        name = pointer.read_text(encoding="utf-8").strip()
        pointed = folder / name
        if pointed.exists():
            baseline_path = str(pointed)

    if candidate_path:
        candidate = candidate_path
    else:
        # **排除基线自己**，否则等于什么都没比：
        # 拿基线和自己比会输出"无变化"，看起来像"没问题"（曾踩过）。
        candidates = [
            path for path in files if path != baseline_path
        ]
        if not candidates:
            raise SystemExit(
                "除基线外没有可比的报告。跑一次评测即可。"
            )
        candidate = candidates[-1]

    if baseline_path is None:
        # 没有指针时退化为"候选的前一份"
        if candidate in files:
            index = files.index(candidate)
            if index > 0:
                baseline_path = files[index - 1]

    if baseline_path is None or baseline_path == candidate:
        raise SystemExit(
            "无法确定基线：请用 --baseline 指定另一份报告。"
        )

    return baseline_path, candidate


def resolve_baseline_in(
    folder: Path,
    candidate_path: str | None,
) -> tuple[str, str]:
    """自测用的薄封装：在指定目录里解析。"""

    return resolve_baseline(candidate_path, reports_dir=folder)


def _make_report(
    model: str = "test-model",
    rules: dict | None = None,
    tiers: dict | None = None,
    errored: int = 0,
    latency: float = 30000.0,
) -> dict:
    """构造一份合成报告，供自测使用。"""

    tiers = tiers or {}
    rule_names = (
        list(rules) if rules else ["R1_missing_point_coverage"]
    )

    turns = []
    for index, case_id in enumerate(
        ("case_a", "case_b")
    ):
        tier = tiers.get(case_id, "B")
        turns.append(
            {
                "case_id": case_id,
                "turn_index": 1,
                "status": "failed" if index < errored else "success",
                "latency_ms": latency,
                "passed": all(
                    (rules or {}).get(name, True)
                    for name in rule_names
                ),
                "analysis": {
                    "answer_quality": 70,
                    "technical_depth": 70,
                    "completeness": 70,
                    "anchors": {
                        "quality": {"anchor": tier},
                        "depth": {"anchor": tier},
                        "completeness": {"anchor": tier},
                    },
                },
                "rules": {
                    name: {
                        "passed": (rules or {}).get(name, True),
                        "missed": (
                            []
                            if (rules or {}).get(name, True)
                            else ["something"]
                        ),
                    }
                    for name in rule_names
                },
            }
        )

    cases = {}
    for turn in turns:
        cases.setdefault(turn["case_id"], []).append(turn)

    return {
        "_path": f"synthetic_{model}.json",
        "run_config": {
            "llm_model": model,
            "llm_base_url": "http://test",
            "analyzer_temperature": 0.2,
            "follow_up_temperature": 0.4,
            "sampling": {
                "offline_analysis_samples": 3,
                "offline_evaluation_samples": 3,
            },
        },
        "summary": {
            "case_count": len(cases),
            "passed_case_count": len(cases),
            "turn_count": len(turns),
            "turn_pass_count": sum(
                1 for turn in turns if turn["passed"]
            ),
            "errored_turn_count": errored,
            "rule_pass_rate": {
                name: (
                    sum(
                        1
                        for turn in turns
                        if turn["rules"][name]["passed"]
                    )
                    / len(turns)
                )
                for name in rule_names
            },
            "r1_average_hit_rate": 0.5,
            "r3_average_hit_rate": 0.4,
            "latency_ms": {"average": latency, "max": latency},
        },
        "cases": [
            {
                # case_id 必须写在 case 层：index_turns 用它做键。
                # 漏掉会让所有 case 共用 None 键、互相覆盖 ——
                # 自测曾因此只看到一轮（见 _run_self_test）。
                "case_id": case_id,
                "turns": case_turns,
            }
            for case_id, case_turns in cases.items()
        ],
    }


def _run_self_test() -> int:
    """自测：验证对比逻辑在合成报告上的行为。

    本工具会频繁用于判断"换模型有没有变差"，
    它自己出错会直接误导基线判断，因此必须有自测。
    """

    import contextlib
    import io

    checks: list[tuple[str, bool]] = []

    def check(label: str, ok: bool) -> None:
        checks.append((label, ok))
        print(f"  [{'OK  ' if ok else 'FAIL'}] {label}")

    def run(base: dict, cand: dict) -> tuple[int, str]:
        buffer = io.StringIO()
        with contextlib.redirect_stdout(buffer):
            differing = print_header(base, cand)
            summary = print_summary(base, cand)
            changed = print_turns(
                base, cand, scores_comparable=not differing
            )
            print_rule_failures(base, cand)
            code = print_verdict(
                base, cand, summary, differing, changed
            )
        return code, buffer.getvalue()

    print("=" * 70)
    print("report_compare 自测")
    print("=" * 70)

    # 1. 完全相同 → 退出码 0，无档位变化
    same_code, same_out = run(
        _make_report(), _make_report()
    )
    check("完全相同时退出码为 0", same_code == 0)
    check(
        "完全相同时报告无档位变化",
        "档位/判定无变化" in same_out,
    )

    # 2. 规则倒退 → 退出码 1
    regressed_code, regressed_out = run(
        _make_report(rules={"R1_missing_point_coverage": True}),
        _make_report(rules={"R1_missing_point_coverage": False}),
    )
    check("规则倒退时退出码为 1", regressed_code == 1)
    check(
        "规则倒退被明确指出",
        "规则倒退" in regressed_out,
    )

    # 3. 出错轮次 → 退出码 2（优先级高于规则倒退）
    error_code, error_out = run(
        _make_report(),
        _make_report(errored=1),
    )
    check("出现出错轮次时退出码为 2", error_code == 2)
    check(
        "出错轮次被指出",
        "出错轮次" in error_out,
    )

    # 4. 口径相同时，档位变化不被免责
    tier_code, tier_out = run(
        _make_report(tiers={"case_a": "B"}),
        _make_report(tiers={"case_a": "C"}),
    )
    check(
        "口径相同时档位变化被列出",
        "case_a#1" in tier_out,
    )
    check(
        "口径相同时不写「不能解读」",
        "不能**解读" not in tier_out
        and "不能**" not in tier_out,
    )

    # 5. 口径不同时，档位变化被免责
    #
    # 必须**同时**有口径差异与档位差异才测得到这条分支：
    # 只换模型名、档位不变的话，根本不会打印免责说明。
    diff_code, diff_out = run(
        _make_report(
            model="model-x", tiers={"case_a": "B"}
        ),
        _make_report(
            model="model-y", tiers={"case_a": "C"}
        ),
    )
    check(
        "口径不同被识别为不可比",
        "不可直接比较" in diff_out,
    )
    check(
        "口径不同时档位变化被免责",
        "解读为模型变好或变差" in diff_out,
    )

    # 6. 逐轮索引能处理同名 case 的多轮
    multi = _make_report()
    multi["cases"][0]["turns"].append(
        dict(multi["cases"][0]["turns"][0], turn_index=2)
    )
    check(
        "同一 case 的多轮不会被覆盖",
        len(index_turns(multi)) == 3,
    )

    # 7. 档位提取走的是 anchors 的嵌套结构（不是直接映射）
    sample = _make_report(tiers={"case_a": "A"})
    check(
        "档位提取正确（嵌套 anchors.quality.anchor）",
        tier_of(sample["cases"][0]["turns"][0], "quality") == "A",
    )

    # 8. 自动选择候选时不会拿基线自己来比
    #
    # 这是实测踩到的 bug：把指针指向的报告又当成候选，
    # 会输出"无变化"，看起来像"没问题"，实际什么都没比。
    #
    # 用注入目录的方式测，而不是改模块常量 ——
    # 后者会被 resolve_baseline 读到的旧名字绕过。
    import tempfile

    with tempfile.TemporaryDirectory() as tmp:
        folder = Path(tmp)
        older = folder / "interview_evaluation_20260101_000000.json"
        newer = folder / "interview_evaluation_20260102_000000.json"
        older.write_text("{}", encoding="utf-8")
        newer.write_text("{}", encoding="utf-8")
        (folder / "BASELINE").write_text(
            newer.name, encoding="utf-8"
        )

        auto_base, auto_cand = resolve_baseline_in(folder, None)

        check(
            "自动候选排除基线自己"
            f"（base={Path(auto_base).name}，"
            f"cand={Path(auto_cand).name}）",
            auto_base == str(newer) and auto_cand == str(older),
        )

    print()
    passed = sum(1 for _, ok in checks if ok)
    print(f"通过 {passed} / {len(checks)}")

    if passed != len(checks):
        for label, ok in checks:
            if not ok:
                print(f"  失败: {label}")
        return 1

    return 0


def main() -> int:
    parser = argparse.ArgumentParser(
        description=(
            "对比两份面试评测报告（纯读 JSON，不调 LLM）"
        ),
    )
    parser.add_argument(
        "--baseline",
        metavar="PATH",
        help="基线报告路径；不传则用 BASELINE 指针或上一份报告",
    )
    parser.add_argument(
        "--candidate",
        metavar="PATH",
        help="候选报告路径；不传则用最新一份",
    )
    parser.add_argument(
        "--set-baseline",
        metavar="PATH",
        help=(
            "把指定报告设为基线（写 BASELINE 指针文件）后退出。"
            "换模型的正确做法：确认新报告没问题后，"
            "把它设为新基线"
        ),
    )
    parser.add_argument(
        "--json",
        action="store_true",
        help="以 JSON 输出，便于脚本消费",
    )
    parser.add_argument(
        "--self-test",
        action="store_true",
        help="用合成报告自测对比逻辑，不读取真实报告",
    )

    args = parser.parse_args()

    if args.self_test:
        return _run_self_test()

    if args.set_baseline:
        path = Path(args.set_baseline)
        if not path.exists():
            raise SystemExit(f"报告不存在: {path}")
        BASELINE_POINTER.write_text(
            path.name, encoding="utf-8"
        )
        print(f"已把基线设为: {path.name}")
        return 0

    if args.baseline:
        baseline_path = args.baseline
        candidate_path = args.candidate or sorted(
            glob.glob(
                str(REPORTS_DIR / "interview_evaluation_*.json")
            )
        )[-1]
    else:
        baseline_path, candidate_path = resolve_baseline(
            args.candidate
        )

    baseline = load_report(baseline_path)
    candidate = load_report(candidate_path)

    if args.json:
        base_turns = index_turns(baseline)
        cand_turns = index_turns(candidate)
        changed = []

        for key in sorted(set(base_turns) & set(cand_turns)):
            for dimension, field in DIMENSIONS:
                if tier_of(base_turns[key], dimension) != tier_of(
                    cand_turns[key], dimension
                ):
                    changed.append(
                        {
                            "turn": key,
                            "dimension": dimension,
                            "baseline_tier": tier_of(
                                base_turns[key], dimension
                            ),
                            "candidate_tier": tier_of(
                                cand_turns[key], dimension
                            ),
                        }
                    )

        print(
            json.dumps(
                {
                    "baseline": os.path.basename(baseline_path),
                    "candidate": os.path.basename(candidate_path),
                    "baseline_summary": baseline.get("summary"),
                    "candidate_summary": candidate.get("summary"),
                    "tier_changes": changed,
                },
                ensure_ascii=False,
                indent=2,
            )
        )
        return 0

    differing = print_header(baseline, candidate)
    summary = print_summary(baseline, candidate)
    changed_turns = print_turns(
        baseline,
        candidate,
        scores_comparable=not differing,
    )
    print_rule_failures(baseline, candidate)

    return print_verdict(
        baseline,
        candidate,
        summary,
        differing,
        changed_turns,
    )


if __name__ == "__main__":
    sys.exit(main())
