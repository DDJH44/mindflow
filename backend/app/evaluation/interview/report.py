"""Interview Engine 评估的报告层。

职责：把 runner 的结果汇总成可比较的报告，并落盘到
evaluation_reports/interview_evaluation_*.json。

报告里会写入运行所依赖的模型与温度配置，
这样两次报告的分数差异可以追溯到具体变量，
而不是只看到"分数变了"。
"""

import json
from datetime import datetime
from pathlib import Path

from app.core.config import settings
from app.services.interview.answer_analyzer import (
    offline_analysis_samples,
    online_analysis_samples,
)
from app.services.interview.evaluator import (
    offline_evaluation_samples,
    online_evaluation_samples,
)

# 分析器与追问生成器当前使用的温度（与 services 层保持一致）。
# 这里显式记录，方便在报告中对比不同温度下的稳定性。
ANALYZER_TEMPERATURE = 0.2
FOLLOW_UP_TEMPERATURE = 0.4

DATASET_VERSION = "v1"

# 报告输出目录：backend/evaluation_reports
# 从本文件向上一共 4 层：interview -> evaluation -> app -> backend
REPORT_DIR = Path(__file__).resolve().parents[3] / "evaluation_reports"


def _average(values: list[float]) -> float:
    if not values:
        return 0.0

    return round(sum(values) / len(values), 4)


def summarize(results: list[dict]) -> dict:
    """汇总全部 case 的指标"""

    total_cases = len(results)

    passed_cases = [
        item for item in results if item.get("passed")
    ]
    failed_cases = [
        item for item in results if not item.get("passed")
    ]

    all_turns = [
        turn
        for item in results
        for turn in item.get("turns", [])
    ]

    successful_turns = [
        turn for turn in all_turns if turn.get("status") == "success"
    ]
    errored_turns = [
        turn for turn in all_turns if turn.get("status") == "failed"
    ]

    # ---- 各规则的通过数 ----
    rule_pass_counts: dict[str, int] = {}
    rule_total_counts: dict[str, int] = {}

    for turn in successful_turns:
        for name, rule in turn.get("rules", {}).items():
            # 已废弃的规则不计入通过率。
            # 否则会用一个无法成立的判据把整体通过率拉低，
            # 制造假的失败信号（见 §8A.7 D4）。
            if rule.get("deprecated"):
                continue

            rule_total_counts[name] = rule_total_counts.get(name, 0) + 1
            if rule["passed"]:
                rule_pass_counts[name] = (
                    rule_pass_counts.get(name, 0) + 1
                )

    rule_pass_rate = {
        name: round(
            rule_pass_counts.get(name, 0) / total,
            4,
        )
        for name, total in rule_total_counts.items()
        if total
    }

    # ---- 命中率（连续指标，不只看通过/失败） ----
    r1_rates = [
        turn["rules"]["R1_missing_point_coverage"]["hit_rate"]
        for turn in successful_turns
        if "R1_missing_point_coverage" in turn.get("rules", {})
        and turn["rules"]["R1_missing_point_coverage"]["expected"]
    ]

    r3_rates = [
        turn["rules"]["R3_follow_up_targeting"]["hit_rate"]
        for turn in successful_turns
        if "R3_follow_up_targeting" in turn.get("rules", {})
        and turn["rules"]["R3_follow_up_targeting"]["expected_keywords"]
    ]

    # R2 已废弃：这里保留字段仅为与旧报告对比，
    # 不参与通过判定，也不应出现在摘要的"违规"结论里。
    r2_violations = [
        keyword
        for turn in successful_turns
        if not turn["rules"]
        .get("R2_no_false_missing", {})
        .get("deprecated")
        for keyword in turn["rules"]
        .get("R2_no_false_missing", {})
        .get("violated", [])
    ]

    deprecated_rules = sorted(
        {
            name
            for turn in successful_turns
            for name, rule in turn.get("rules", {}).items()
            if rule.get("deprecated")
        }
    )

    vague_follow_ups = [
        turn["turn_index"]
        for turn in successful_turns
        if turn["rules"]
        .get("R3_follow_up_targeting", {})
        .get("is_vague")
    ]

    latencies = [
        turn["latency_ms"]
        for turn in successful_turns
        if "latency_ms" in turn
    ]

    return {
        "case_count": total_cases,
        "passed_case_count": len(passed_cases),
        "failed_case_count": len(failed_cases),
        "turn_count": len(all_turns),
        "successful_turn_count": len(successful_turns),
        "errored_turn_count": len(errored_turns),
        "turn_pass_count": len(
            [
                turn
                for turn in successful_turns
                if turn.get("passed")
            ]
        ),
        "turn_pass_rate": (
            round(
                len(
                    [
                        turn
                        for turn in successful_turns
                        if turn.get("passed")
                    ]
                )
                / len(successful_turns),
                4,
            )
            if successful_turns
            else 0.0
        ),
        "rule_pass_rate": rule_pass_rate,
        "r1_average_hit_rate": _average(r1_rates),
        "r3_average_hit_rate": _average(r3_rates),
        "r2_violated_keywords": r2_violations,
        "deprecated_rules": deprecated_rules,
        "vague_follow_up_turns": vague_follow_ups,
        "latency_ms": {
            "average": _average(latencies),
            "max": round(max(latencies), 2) if latencies else 0.0,
        },
        "failed_cases": [
            item["case_id"] for item in failed_cases
        ],
    }


def save_report(
    results: list[dict],
    summary: dict,
    dataset: list[dict],
) -> str:
    """把报告写入 evaluation_reports 目录"""

    report = {
        "evaluation": {
            "kind": "interview_engine",
            "version": "v1",
            "dataset_version": DATASET_VERSION,
            "created_at": datetime.now().isoformat(),
            "case_count": len(results),
        },
        "run_config": {
            "llm_model": settings.llm_model or "(default)",
            "llm_base_url": settings.llm_base_url or "(default)",
            "analyzer_temperature": ANALYZER_TEMPERATURE,
            "follow_up_temperature": FOLLOW_UP_TEMPERATURE,
            # 本报告（离线评估）每轮的 LLM 调用次数
            # = 离线分析采样数 + 1 次追问。
            "sampling": {
                "used_in_this_report": "offline",
                "offline_analysis_samples": offline_analysis_samples(),
                "offline_evaluation_samples": (
                    offline_evaluation_samples()
                ),
                "online_analysis_samples": online_analysis_samples(),
                "online_evaluation_samples": (
                    online_evaluation_samples()
                ),
                "llm_calls_per_turn": (
                    offline_analysis_samples() + 1
                ),
            },
            # ⚠️ 线上与离线使用不同采样数，因此
            # **线上分数与本报告分数不可直接比较**（见 §8A.10）。
            "note": (
                "离线评估使用多次采样以保证可复现；"
                "线上路径只采 "
                f"{online_analysis_samples()} 次以控制延迟，"
                "因此线上分数波动更大，两者不可直接比较。"
            ),
        },
        "summary": summary,
        "dataset": dataset,
        "cases": results,
    }

    REPORT_DIR.mkdir(parents=True, exist_ok=True)

    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")

    report_path = (
        REPORT_DIR / f"interview_evaluation_{timestamp}.json"
    )

    report_path.write_text(
        json.dumps(report, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )

    return str(report_path)


def save_stability_report(result: dict) -> str:
    """把稳定性测试结果写入 evaluation_reports 目录。

    与主评估报告分开命名，避免两类报告混在一起：

    - interview_evaluation_*.json  单次质量评估
    - interview_stability_*.json   多次重复的评分波动
    """

    report = {
        "evaluation": {
            "kind": "interview_stability",
            "version": "v1",
            "dataset_version": DATASET_VERSION,
            "created_at": datetime.now().isoformat(),
        },
        "config": result.get("config", {}),
        "summary": result.get("summary", {}),
        "rank_correlations": result.get("rank_correlations", []),
        "turns": result.get("turns", []),
    }

    REPORT_DIR.mkdir(parents=True, exist_ok=True)

    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")

    report_path = (
        REPORT_DIR / f"interview_stability_{timestamp}.json"
    )

    report_path.write_text(
        json.dumps(report, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )

    return str(report_path)
