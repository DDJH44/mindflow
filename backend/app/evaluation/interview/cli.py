"""Interview Engine 评估入口。

用法：

    uv run python -m app.evaluation.interview.cli
    uv run python -m app.evaluation.interview.cli --list
    uv run python -m app.evaluation.interview.cli --case rag_backend_interview

    # 稳定性测试：同一轮重复跑，测量评分波动
    uv run python -m app.evaluation.interview.cli --stability
    uv run python -m app.evaluation.interview.cli --stability --repeats 5

说明：

- 每个 case 的每一轮会调用 2 次 LLM（分析 + 追问）。
- 稳定性模式会额外把示例回答写入数据库以验证真实链路；
  每次运行新建一个探针会话，不污染数据集里的固定会话。
"""

import argparse
import asyncio
import contextlib
import io
import sys
import traceback

from app.database.session import AsyncSessionLocal
from app.evaluation.interview.datasets import (
    INTERVIEW_EVALUATION_CASES,
)
from app.evaluation.interview.report import (
    save_report,
    save_stability_report,
    summarize,
)
from app.evaluation.interview.runner import (
    InterviewEvaluationRunner,
)
from app.evaluation.interview.stability import (
    DEFAULT_MAX_RANGE,
    DEFAULT_REPEATS,
    run_stability,
)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Interview Engine 评估",
    )

    parser.add_argument(
        "--case",
        action="append",
        dest="case_ids",
        metavar="CASE_ID",
        help="只运行指定 case，可重复传入",
    )

    parser.add_argument(
        "--list",
        action="store_true",
        help="列出所有 case 后退出，不调用 LLM",
    )

    parser.add_argument(
        "--verbose",
        action="store_true",
        help="打印完整过程输出（默认只打印结论）",
    )

    parser.add_argument(
        "--stability",
        action="store_true",
        help="运行稳定性测试：同一轮重复多次，测量评分波动",
    )

    parser.add_argument(
        "--repeats",
        type=int,
        default=DEFAULT_REPEATS,
        help=f"稳定性测试每轮重复次数（默认 {DEFAULT_REPEATS}）",
    )

    parser.add_argument(
        "--max-range",
        type=int,
        default=DEFAULT_MAX_RANGE,
        help=(
            "稳定性判定阈值：允许的评分最大极差"
            f"（默认 {DEFAULT_MAX_RANGE}）"
        ),
    )

    parser.add_argument(
        "--probe",
        action="store_true",
        help=(
            "稳定性测试时额外把示例回答写入数据库以验证真实链路"
            "（需要 PostgreSQL 运行）"
        ),
    )

    parser.add_argument(
        "--seed",
        type=int,
        default=None,
        help=(
            "传给 LLM 的随机种子；端点不支持时会自动忽略。"
            "用于对照实验：固定 seed 能否降低评分方差"
        ),
    )

    return parser.parse_args()


def select_cases(case_ids: list[str] | None) -> list[dict]:
    if not case_ids:
        return INTERVIEW_EVALUATION_CASES

    known = {
        case["id"]: case for case in INTERVIEW_EVALUATION_CASES
    }

    unknown = [item for item in case_ids if item not in known]

    if unknown:
        raise SystemExit(
            f"未知的 case id: {unknown}；"
            f"可选: {sorted(known)}"
        )

    return [known[item] for item in case_ids]


def print_summary(summary: dict) -> None:
    print()
    print("=" * 60)
    print("Interview Engine Evaluation Summary")
    print("=" * 60)

    print(
        f"\nCases: {summary['passed_case_count']} / "
        f"{summary['case_count']} passed"
    )
    print(
        f"Turns: {summary['turn_pass_count']} / "
        f"{summary['turn_count']} passed "
        f"(errored: {summary['errored_turn_count']})"
    )

    print("\nRule pass rate:")
    for name, rate in summary["rule_pass_rate"].items():
        print(f"  {name:<32} {rate:.4f}")

    print(
        f"\nR1 平均命中率: {summary['r1_average_hit_rate']:.4f}"
    )
    print(
        f"R3 平均命中率: {summary['r3_average_hit_rate']:.4f}"
    )

    if summary.get("deprecated_rules"):
        print(
            f"\n已废弃规则（不计入通过率）: "
            f"{summary['deprecated_rules']}"
        )

    if summary["vague_follow_up_turns"]:
        print(
            f"R3 泛泛提问的轮次: "
            f"{summary['vague_follow_up_turns']}"
        )

    print(
        f"\nLatency(ms): avg="
        f"{summary['latency_ms']['average']} "
        f"max={summary['latency_ms']['max']}"
    )

    if summary["failed_cases"]:
        print(f"\n未通过的 case: {summary['failed_cases']}")


def print_stability_summary(result: dict) -> None:
    summary = result["summary"]
    config = result["config"]

    print()
    print("=" * 66)
    print("Interview Engine Stability Summary")
    print("=" * 66)

    print(
        f"\nModel: {config['model']} | "
        f"repeats={config['repeats']} | "
        f"threshold={config['max_range_threshold']} | "
        f"seed={config['seed']}"
    )

    print(
        f"\n稳定轮次: {summary['stable_turn_count']} / "
        f"{summary['turn_count']}"
    )

    if summary.get("inconclusive_turn_count"):
        print(
            f"结论不成立（样本不足）: "
            f"{summary['inconclusive_turn_count']}"
        )

    print(f"最大极差: {summary['max_range_observed']}")

    print("\n各轮评分波动:")
    for item in result["turns"]:
        flags = {
            "stable": "稳定",
            "unstable": "不稳定",
            "inconclusive": "样本不足",
        }.get(item["verdict"], item["verdict"])

        print(
            f"  turn {item['turn_index']} [{flags}] "
            f"max_range={item['max_range']} "
            f"覆盖={item['sample_coverage']:.0%} "
            f"({item['successful_repeats']}/{config['repeats']}) "
            f"采样={item.get('analyzer_sample_counts')} "
            f"缺口数波动={item['missing_points_count_range']}"
        )

        for field, stat in item["score_statistics"].items():
            print(
                f"    {field:<18} "
                f"{stat['values']} "
                f"极差={stat['range']} 均值={stat['average']}"
            )

    if result["rank_correlations"]:
        print(
            f"\n评分序列相关性"
            f"（样本 {config['repeats']} 个）:"
        )
        for item in result["rank_correlations"]:
            value = item["quality_vs_depth"]
            shown = (
                "无定义（存在常数序列）"
                if value is None
                else value
            )
            print(
                f"  turn {item['turn_index']} "
                f"quality_vs_depth={shown}"
            )

    if summary["unstable_turns"]:
        print("\n不稳定轮次:")
        for item in summary["unstable_turns"]:
            print(
                f"  {item['case_id']} turn "
                f"{item['turn_index']}: "
                f"max_range={item['max_range']}"
            )

    if summary.get("inconclusive_turns"):
        print("\n样本不足、结论不成立的轮次:")
        for item in summary["inconclusive_turns"]:
            print(
                f"  {item['case_id']} turn "
                f"{item['turn_index']}: "
                f"覆盖={item['sample_coverage']:.0%} "
                f"({item['successful_repeats']} 成功 / "
                f"{item['failed_repeats']} 失败)"
            )


async def run(cases: list[dict], verbose: bool) -> int:
    runner = InterviewEvaluationRunner(cases=cases)

    if verbose:
        results = await runner.run()
    else:
        # 屏蔽过程输出，只保留最终摘要
        with contextlib.redirect_stdout(io.StringIO()):
            results = await runner.run()

    summary = summarize(results)

    report_path = save_report(
        results=results,
        summary=summary,
        dataset=cases,
    )

    print_summary(summary)
    print(f"\nReport: {report_path}")

    return 0 if summary["failed_case_count"] == 0 else 1


async def run_stability_mode(
    case_ids: list[str] | None,
    repeats: int,
    max_range: int,
    verbose: bool,
    probe: bool,
    seed: int | None,
) -> int:
    async def execute(db):
        return await run_stability(
            repeats=repeats,
            max_range=max_range,
            case_ids=case_ids,
            write_probe=probe,
            db=db,
            seed=seed,
        )

    if probe:
        # 只有需要写库时才连数据库
        async with AsyncSessionLocal() as db:
            if verbose:
                result = await execute(db)
            else:
                with contextlib.redirect_stdout(io.StringIO()):
                    result = await execute(db)
    elif verbose:
        result = await execute(None)
    else:
        with contextlib.redirect_stdout(io.StringIO()):
            result = await execute(None)

    report_path = save_stability_report(result)

    print_stability_summary(result)
    print(f"\nReport: {report_path}")

    # 退出码语义：
    #   0 = 全部稳定且样本充足
    #   1 = 存在不稳定轮次
    #   2 = 存在样本不足、结论不成立的轮次（需重跑）
    if result["summary"].get("inconclusive_turn_count"):
        return 2

    return (
        0
        if result["summary"]["unstable_turn_count"] == 0
        else 1
    )


def main() -> None:
    args = parse_args()

    if args.list:
        print("可用的 case：")
        for case in INTERVIEW_EVALUATION_CASES:
            print(
                f"  {case['id']:<24} "
                f"{len(case['turns'])} 轮 | {case['title']}"
            )
        return

    try:
        if args.stability:
            exit_code = asyncio.run(
                run_stability_mode(
                    case_ids=args.case_ids,
                    repeats=args.repeats,
                    max_range=args.max_range,
                    verbose=args.verbose,
                    probe=args.probe,
                    seed=args.seed,
                )
            )
        else:
            cases = select_cases(args.case_ids)
            exit_code = asyncio.run(
                run(cases, verbose=args.verbose)
            )
    except KeyboardInterrupt:
        print("\n已中断", file=sys.stderr)
        raise SystemExit(130)
    except Exception:
        traceback.print_exc()
        raise SystemExit(1)

    raise SystemExit(exit_code)


if __name__ == "__main__":
    main()
