"""Interview Engine 评估的执行层。

职责：把数据集里的每个 case、每一轮，真正交给
InterviewAnswerAnalyzer 和 InterviewFollowUpGenerator 跑一遍，
再用 judges 里的规则判定，最后产出结构化结果。

成本控制：

- 每一轮调用 LLM 的次数 = 离线分析采样数 + 1 次追问
  （默认 3 + 1 = 4 次）。规则判定全在本地，不额外消耗 token。
- 这里显式使用**离线**采样数。线上路径只采 1 次以控制延迟，
  **两条路径的分数不可直接比较**（见 MIND_FLOW_PLAN.md §8A.10）。

错误隔离：

- 单轮失败不会中断整个评估，会记为该轮 failed 并继续。
- 这样一次运行就能看到全部用例的状态，而不是第一个错误就退出。
"""

import time

from app.evaluation.interview.judges import (
    evaluate_analyzer_score_floor,
    evaluate_follow_up_targeting,
    evaluate_forbidden_missing_points,
    evaluate_missing_point_coverage,
)
from app.services.interview.answer_analyzer import (
    InterviewAnswerAnalyzer,
    offline_analysis_samples,
)
from app.services.interview.follow_up_generator import (
    InterviewFollowUpGenerator,
)


class InterviewEvaluationRunner:
    """按数据集逐轮执行面试评估"""

    def __init__(self, cases: list[dict]):
        self.cases = cases
        self.analyzer = InterviewAnswerAnalyzer()
        self.follow_up_generator = InterviewFollowUpGenerator()

    async def run_turn(
        self,
        turn: dict,
        case: dict,
        turn_index: int,
    ) -> dict:
        """执行一轮：分析回答 -> 生成追问 -> 规则判定"""

        base = {
            "turn_index": turn_index,
            "question": turn["question"],
            "answer": turn["answer"],
            "case_id": case["id"],
        }

        started = time.perf_counter()

        try:
            analysis = await self.analyzer.analyze(
                question=turn["question"],
                answer=turn["answer"],
                # 显式使用离线采样数：评测需要可复现的分数。
                # 不能依赖 analyzer 的默认值 —— 那是线上配置（1 次）。
                repeats=offline_analysis_samples(),
            )

            follow_up_question = (
                await self.follow_up_generator.generate_follow_up(
                    question=turn["question"],
                    answer=turn["answer"],
                    analysis=analysis,
                )
            )

        except Exception as exc:
            return {
                **base,
                "status": "failed",
                "error_type": type(exc).__name__,
                "error": str(exc),
                "latency_ms": round(
                    (time.perf_counter() - started) * 1000,
                    2,
                ),
            }

        latency_ms = round(
            (time.perf_counter() - started) * 1000,
            2,
        )

        # ---------------- 规则判定（全部本地完成） ----------------

        expected = turn.get("expected_missing_points", [])
        forbidden = turn.get("forbidden_missing_points", [])
        follow_up_keywords = turn.get(
            "expected_follow_up_keywords",
            [],
        )

        r1 = evaluate_missing_point_coverage(
            missing_points=analysis.missing_points,
            expected=expected,
        )

        r2 = evaluate_forbidden_missing_points(
            missing_points=analysis.missing_points,
            forbidden=forbidden,
        )

        r3 = evaluate_follow_up_targeting(
            follow_up_question=follow_up_question,
            expected_keywords=follow_up_keywords,
        )

        min_score = turn.get("min_analyzer_score")
        r4 = (
            evaluate_analyzer_score_floor(
                answer_quality=analysis.answer_quality,
                technical_depth=analysis.technical_depth,
                completeness=analysis.completeness,
                min_score=min_score,
            )
            if min_score is not None
            else None
        )

        rules = {
            "R1_missing_point_coverage": r1,
            "R3_follow_up_targeting": r3,
        }

        # R2 已废弃（见 judges.evaluate_forbidden_missing_points 的说明）。
        # 仅在数据集仍提供 forbidden 列表时记录结果供旧报告对比，
        # **不参与通过判定**，避免用无法成立的关键词判据否决整轮。
        if forbidden:
            rules["R2_no_false_missing"] = r2

        if r4 is not None:
            rules["R4_analyzer_score_floor"] = r4

        passed = all(
            rule["passed"]
            for rule in rules.values()
            if not rule.get("deprecated")
        )

        return {
            **base,
            "status": "success",
            "latency_ms": latency_ms,
            "passed": passed,
            "analysis": {
                "answer_quality": analysis.answer_quality,
                "technical_depth": analysis.technical_depth,
                "completeness": analysis.completeness,
                "anchors": analysis.anchor_summary(),
                "sample_count": analysis.sample_count,
                # 去重前的每样本缺口，用于诊断去重效果与上限截断
                "sampled_missing_points": (
                    analysis.sampled_missing_points
                ),
                "strengths": analysis.strengths,
                "missing_points": analysis.missing_points,
                "summary": analysis.summary,
            },
            "follow_up_question": follow_up_question,
            "rules": rules,
        }

    async def run_case(self, case: dict) -> dict:
        """执行一个 case 的全部轮次"""

        turns = []

        for index, turn in enumerate(case["turns"], start=1):
            print(
                f"  第 {index} 轮 | 问题: {turn['question'][:40]}..."
            )

            result = await self.run_turn(
                turn=turn,
                case=case,
                turn_index=index,
            )

            turns.append(result)

            if result["status"] == "failed":
                print(
                    f"    失败: {result['error_type']}: "
                    f"{result['error']}"
                )
                continue

            marks = " ".join(
                f"{name.split('_')[0]}="
                f"{'PASS' if rule['passed'] else 'FAIL'}"
                for name, rule in result["rules"].items()
            )

            print(
                f"    {marks} | "
                f"scores="
                f"{result['analysis']['answer_quality']}/"
                f"{result['analysis']['technical_depth']}/"
                f"{result['analysis']['completeness']} | "
                f"{result['latency_ms']} ms"
            )

        successful = [
            turn for turn in turns if turn["status"] == "success"
        ]

        return {
            "case_id": case["id"],
            "title": case["title"],
            "project_id": case.get("project_id"),
            "direction": case.get("direction"),
            "turn_count": len(turns),
            "successful_turn_count": len(successful),
            "failed_turn_count": len(turns) - len(successful),
            "passed": bool(successful)
            and all(turn["passed"] for turn in successful)
            and len(successful) == len(turns),
            "turns": turns,
        }

    async def run(self) -> list[dict]:
        """执行全部 case"""

        results = []

        for case in self.cases:
            print()
            print("=" * 60)
            print(f"Case: {case['id']} | {case['title']}")
            print("=" * 60)

            try:
                results.append(await self.run_case(case))
            except Exception as exc:
                print(
                    f"Case 执行异常: {type(exc).__name__}: {exc}"
                )
                results.append({
                    "case_id": case["id"],
                    "title": case.get("title"),
                    "turn_count": len(case.get("turns", [])),
                    "successful_turn_count": 0,
                    "failed_turn_count": len(case.get("turns", [])),
                    "passed": False,
                    "status": "failed",
                    "error_type": type(exc).__name__,
                    "error": str(exc),
                    "turns": [],
                })

        return results
