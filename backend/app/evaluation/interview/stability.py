"""Interview Engine 评估的稳定性层。

目的：回答"同一个输入重复跑，分数会不会飘"。

这是原计划里最担心的问题：
    第一次 52 分、第二次 34 分、第三次 67 分

做法：

1. 每个数据集轮次重复运行 N 次（默认 3 次）。
2. 记录 answer_quality / technical_depth / completeness 的每次取值。
3. 计算极差（max - min）。
4. 极差超过阈值即判定该轮"不稳定"。

额外输出 Spearman 等级相关系数：

- 极差只看波动幅度，不区分"整体平移"和"严格单调漂移"。
- rank_correlation 接近 1 表示评分顺序一致，
  接近 0 或负数表示模型对同一批样本的排序都不稳定。
- 只有 3 个样本时该系数没有统计意义，仅作为观察值记录，
  不参与通过判定。

注意：

- 评分测量本身不依赖数据库：analyze() 与 generate_follow_up()
  都只调用 LLM。
- 可选的数据库探针（write_probe=True）会额外把示例回答写入
  PostgreSQL，用来顺带验证真实链路；数据库不可用时自动跳过，
  不影响评分测量。
"""

import time

from app.evaluation.interview.datasets import (
    INTERVIEW_EVALUATION_CASES,
)
from app.services.interview.answer_analyzer import (
    InterviewAnswerAnalyzer,
    offline_analysis_samples,
    online_analysis_samples,
)
from app.services.interview.evaluator import (
    offline_evaluation_samples,
    online_evaluation_samples,
)
from app.services.interview.follow_up_generator import (
    InterviewFollowUpGenerator,
)
from app.services.openai_llm_service import OpenAILLMService

# 评分稳定性的极差阈值。
# 实测基线（repeats=10，见 §8A.5）：
#   完整专业回答（均值 89）极差 5-13
#   答非所问（均值 8）     极差 0-5
#   中间质量（均值 35-71） 极差 15-20
# 也就是说波动与回答质量的极端程度成反比：
# 中间质量的回答模型判断天然摇摆。
# 因此 15 分阈值只适用于两端质量的回答；
# 对中间质量回答会误报，需按 case 单独标定。
DEFAULT_MAX_RANGE = 15

DEFAULT_REPEATS = 3

# 样本覆盖率下限。
# 低于该比例说明失败样本过多，极差不可信，
# 判定结果标记为 inconclusive 而不是 stable。
MIN_COVERAGE = 0.8

SCORE_FIELDS = (
    "answer_quality",
    "technical_depth",
    "completeness",
)


def _rank(values: list[int]) -> list[float]:
    """计算带并列处理的平均秩。"""

    order = sorted(range(len(values)), key=lambda i: values[i])
    ranks = [0.0] * len(values)
    index = 0

    while index < len(order):
        end = index
        while (
            end + 1 < len(order)
            and values[order[end + 1]] == values[order[index]]
        ):
            end += 1

        average_rank = (index + end) / 2 + 1

        for position in range(index, end + 1):
            ranks[order[position]] = average_rank

        index = end + 1

    return ranks


def spearman_rank_correlation(
    left: list[int],
    right: list[int],
) -> float | None:
    """Spearman 等级相关系数。

    返回 None 表示**在该序列上无定义**，而不是"不相关"：

    - 样本少于 2 个；
    - 任一序列为常数（例如 technical_depth 恒为 0）。
      此时秩全相同，分母为 0，相关系数在数学上无定义。
      若返回 0.0，会被误读成"两个维度不相关"。
    """

    if len(left) != len(right) or len(left) < 2:
        return None

    # 常数序列：相关性无定义
    if len(set(left)) == 1 or len(set(right)) == 1:
        return None

    left_ranks = _rank(left)
    right_ranks = _rank(right)

    mean_left = sum(left_ranks) / len(left_ranks)
    mean_right = sum(right_ranks) / len(right_ranks)

    numerator = sum(
        (a - mean_left) * (b - mean_right)
        for a, b in zip(left_ranks, right_ranks)
    )

    denominator = (
        sum((a - mean_left) ** 2 for a in left_ranks) ** 0.5
        * sum((b - mean_right) ** 2 for b in right_ranks) ** 0.5
    )

    if denominator == 0:
        return None

    return round(numerator / denominator, 4)


async def _create_probe_session(
    db,
    project_id: int,
) -> tuple[int, int]:
    """新建一个专用面试会话与问题，返回 (session_id, question_id)。

    仅在 write_probe=True 时调用。
    每次运行都新建，避免污染数据集里的固定会话，
    也避免示例回答把固定 session 的问题序列打乱。
    """

    from app.services.interview_question_service import (
        InterviewQuestionService,
    )
    from app.services.interview_session_service import (
        InterviewSessionService,
    )

    # 会话必须记录所有者，因此取项目的 owner_id。
    # 这里用显式查询而不是让 service 去猜：
    # 这是**测试脚手架**，不是业务路径，
    # 它清楚地表明"探针会话挂在项目所有者名下"。
    from sqlalchemy import select as _select

    from app.models.project import Project

    owner_id = (
        await db.execute(
            _select(Project.owner_id).where(Project.id == project_id)
        )
    ).scalar_one()

    session = await InterviewSessionService(db).create_session(
        project_id=project_id,
        user_id=owner_id,
        interview_type="technical",
    )

    # 占位问题，仅用于给示例回答提供一个可挂载的 question_id
    question = await InterviewQuestionService(db).create_question(
        session_id=session.id,
        question="（稳定性测试占位问题）",
        question_type="technical",
        question_index=0,
        context="stability probe",
    )

    return session.id, question.id


async def run_turn_stability(
    question: str,
    answer: str,
    repeats: int,
    db=None,
    project_id: int | None = None,
    question_id: int | None = None,
    write_probe: bool = False,
    seed: int | None = None,
) -> dict:
    """对同一轮输入重复运行，测量评分波动。

    write_probe=True 时额外把示例回答写入数据库以验证真实链路；
    该能力不参与评分测量。

    seed 会透传给 LLM，用于观察固定随机种子能否降低方差。
    """

    analyzer = InterviewAnswerAnalyzer()
    generator = InterviewFollowUpGenerator()

    samples = []

    for index in range(1, repeats + 1):
        started = time.perf_counter()

        try:
            analysis = await analyzer.analyze(
                question=question,
                answer=answer,
                seed=seed,
                # 显式使用离线采样数。
                # 不能依赖 analyzer 的默认值 —— 那是线上配置（1 次），
                # 用它测量会把"线上波动"当成"评测基线"。
                repeats=offline_analysis_samples(),
            )

            follow_up = await generator.generate_follow_up(
                question=question,
                answer=answer,
                analysis=analysis,
                seed=seed,
            )

            elapsed = time.perf_counter() - started

            sample = {
                "sample_index": index,
                "status": "success",
                "latency_ms": round(elapsed * 1000, 2),
                "scores": {
                    field: getattr(analysis, field)
                    for field in SCORE_FIELDS
                },
                # 记录分析器内部实际采样了几次。
                # 没有这个字段，就无法从报告本身分辨
                # "samples=3" 与 "samples=5" 的报告，只能靠时间戳猜
                # （已踩过这个坑）。
                "analyzer_sample_count": analysis.sample_count,
                # 锚点是评分的唯一事实源，必须记录，
                # 否则无法判断分数变化来自"跳档"还是"微调"。
                "anchors": analysis.anchor_summary(),
                "missing_points_count": len(
                    analysis.missing_points
                ),
                "missing_points": analysis.missing_points,
                "follow_up_question": follow_up,
            }

            print(
                f"    第 {index} 次 | "
                f"q={sample['scores']['answer_quality']} "
                f"d={sample['scores']['technical_depth']} "
                f"c={sample['scores']['completeness']} | "
                f"缺口={sample['missing_points_count']} | "
                f"{sample['latency_ms']:.0f} ms"
            )

        except Exception as exc:
            sample = {
                "sample_index": index,
                "status": "failed",
                "error_type": type(exc).__name__,
                "error": str(exc),
            }

            print(
                f"    第 {index} 次 | 失败 "
                f"{type(exc).__name__}: {exc}"
            )

        samples.append(sample)

        # 顺带把示例回答写入数据库，验证真实链路可用。
        # 数据库不可用时跳过，不影响评分测量。
        if write_probe and db is not None and question_id is not None:
            from app.services.interview.interview_turn_service import (
                InterviewTurnService,
            )

            try:
                await InterviewTurnService(db).process_answer(
                    question_id=question_id,
                    answer=answer,
                )
            except Exception as exc:
                print(
                    f"      [警告] 写入回答失败: "
                    f"{type(exc).__name__}: {exc}"
                )

    successful = [
        sample
        for sample in samples
        if sample["status"] == "success"
    ]

    statistics = {}

    for field in SCORE_FIELDS:
        values = [
            sample["scores"][field] for sample in successful
        ]

        if not values:
            continue

        statistics[field] = {
            "values": values,
            "min": min(values),
            "max": max(values),
            "range": max(values) - min(values),
            "average": round(sum(values) / len(values), 2),
        }

    ranges = [
        item["range"] for item in statistics.values()
    ]

    max_range = max(ranges) if ranges else 0

    missing_counts = [
        sample["missing_points_count"] for sample in successful
    ]

    # 记录本轮使用的分析器采样数，写入报告摘要，
    # 使报告可以自证"用的是几次采样"。
    analyzer_sample_counts = sorted(
        {
            sample.get("analyzer_sample_count", 0)
            for sample in successful
        }
    )

    return {
        "question": question,
        "answer_length": len(answer),
        "repeats": repeats,
        "analyzer_sample_counts": analyzer_sample_counts,
        "successful_repeats": len(successful),
        "failed_repeats": len(samples) - len(successful),
        "samples": samples,
        "score_statistics": statistics,
        "max_range": max_range,
        "missing_points_count_range": (
            max(missing_counts) - min(missing_counts)
            if missing_counts
            else None
        ),
    }


async def run_stability(
    repeats: int = DEFAULT_REPEATS,
    max_range: int = DEFAULT_MAX_RANGE,
    case_ids: list[str] | None = None,
    write_probe: bool = False,
    db=None,
    seed: int | None = None,
) -> dict:
    """对所有数据集轮次做稳定性测量。

    write_probe=False（默认）时完全不访问数据库，
    因此 PostgreSQL 未启动也能测评分波动。
    """

    cases = INTERVIEW_EVALUATION_CASES

    if case_ids:
        cases = [
            case
            for case in cases
            if case["id"] in case_ids
        ]

    turns = []

    model = OpenAILLMService().model

    for case in cases:
        print()
        print("=" * 66)
        print(f"Case: {case['id']} | {case['title']}")
        print("=" * 66)

        question_id = None
        session_id = None

        if write_probe:
            if db is None:
                raise ValueError(
                    "write_probe=True 需要传入数据库会话"
                )

            session_id, question_id = await _create_probe_session(
                db=db,
                project_id=case["project_id"],
            )
            print(f"  探针会话 session_id={session_id}")
        else:
            print("  数据库探针已关闭（仅测评分波动）")

        for index, turn in enumerate(case["turns"], start=1):
            print(
                f"  第 {index} 轮 | "
                f"{turn['question'][:38]}..."
            )

            result = await run_turn_stability(
                question=turn["question"],
                answer=turn["answer"],
                repeats=repeats,
                db=db,
                project_id=case["project_id"],
                question_id=question_id,
                write_probe=write_probe,
                seed=seed,
            )

            result["turn_index"] = index
            result["case_id"] = case["id"]

            # 判定前提：样本必须基本跑满。
            # 传输抖动会让部分样本失败，若只看成功样本的极差，
            # 少量样本会天然给出"更小"的极差，从而得出
            # "稳定"的错误结论。这正是 ADR-013 想避免的静默掩盖。
            coverage = (
                result["successful_repeats"] / repeats
                if repeats
                else 0.0
            )
            result["sample_coverage"] = round(coverage, 3)
            result["coverage_sufficient"] = coverage >= MIN_COVERAGE

            if not result["coverage_sufficient"]:
                result["verdict"] = "inconclusive"
            elif result["max_range"] <= max_range:
                result["verdict"] = "stable"
            else:
                result["verdict"] = "unstable"

            result["stable"] = result["verdict"] == "stable"

            turns.append(result)

    # 同一轮内"评分之间的相对一致性"：
    # 用 answer_quality 与 technical_depth 的序列排序对比
    rank_correlations = []

    for item in turns:
        quality = [
            sample["scores"]["answer_quality"]
            for sample in item["samples"]
            if sample["status"] == "success"
        ]
        depth = [
            sample["scores"]["technical_depth"]
            for sample in item["samples"]
            if sample["status"] == "success"
        ]

        if len(quality) >= 2 and len(quality) == len(depth):
            rank_correlations.append(
                {
                    "case_id": item["case_id"],
                    "turn_index": item["turn_index"],
                    "quality_vs_depth": spearman_rank_correlation(
                        quality, depth
                    ),
                }
            )

    unstable = [
        item for item in turns if item["verdict"] == "unstable"
    ]

    inconclusive = [
        item for item in turns if item["verdict"] == "inconclusive"
    ]

    return {
        "config": {
            "model": model,
            "repeats": repeats,
            "max_range_threshold": max_range,
            "min_coverage": MIN_COVERAGE,
            "analyzer_temperature": 0.2,
            "follow_up_temperature": 0.4,
            "seed": seed,
            # 同时记录两套采样数。
            # 报告只写"本报告用了哪套"是不够的：
            # 一旦线上与离线用不同的采样数，
            # 两份报告的分数就不可直接比较（见 §8A.10），
            # 必须能从报告本身看出这个差异。
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
            },
        },
        "turns": turns,
        "rank_correlations": rank_correlations,
        "summary": {
            "turn_count": len(turns),
            "stable_turn_count": len(
                [
                    item
                    for item in turns
                    if item["verdict"] == "stable"
                ]
            ),
            "unstable_turn_count": len(unstable),
            "inconclusive_turn_count": len(inconclusive),
            "unstable_turns": [
                {
                    "case_id": item["case_id"],
                    "turn_index": item["turn_index"],
                    "max_range": item["max_range"],
                    "score_statistics": item["score_statistics"],
                }
                for item in unstable
            ],
            "inconclusive_turns": [
                {
                    "case_id": item["case_id"],
                    "turn_index": item["turn_index"],
                    "sample_coverage": item["sample_coverage"],
                    "successful_repeats": item["successful_repeats"],
                    "failed_repeats": item["failed_repeats"],
                }
                for item in inconclusive
            ],
            "max_range_observed": (
                max(item["max_range"] for item in turns)
                if turns
                else 0
            ),
            # 只有样本覆盖率足够时才允许下"稳定"结论
            "conclusive": not inconclusive,
        },
    }
