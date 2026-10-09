import asyncio
import time

import json
from datetime import datetime
from pathlib import Path

from app.database.session import AsyncSessionLocal
from app.evaluation.datasets import EVALUATION_CASES
from app.services.retrieval_service import RetrievalService
from app.evaluation.metrics import (
    filter_accuracy,
    recall_at_k,
    reciprocal_rank,
)


class EvaluationRunner:
    """RAG Evaluation Runner"""

    def __init__(self):
        self.cases = EVALUATION_CASES

    async def run_case(self, case: dict) -> dict:
        start_time = time.perf_counter()

        async with AsyncSessionLocal() as db:
            retrieval_service = RetrievalService(db)

            results = await retrieval_service.retrieve(
                query=case["query"],
                limit=5,
                project_id=case.get("project_id"),
                document_type=case.get("document_type"),
            )

        latency_ms = (
            time.perf_counter() - start_time
        ) * 1000

        retrieved_chunk_ids = [
            item["chunk_id"]
            for item in results
        ]

        retrieved_document_ids = [
            item["document_id"]
            for item in results
        ]

        chunk_recall = {
            "recall@1": recall_at_k(
                retrieved_chunk_ids,
                case["expected_chunk_ids"],
                1,
            ),
            "recall@3": recall_at_k(
                retrieved_chunk_ids,
                case["expected_chunk_ids"],
                3,
            ),
            "recall@5": recall_at_k(
                retrieved_chunk_ids,
                case["expected_chunk_ids"],
                5,
            ),
        }

        document_recall = {
            "recall@1": recall_at_k(
                retrieved_document_ids,
                case["expected_document_ids"],
                1,
            ),
            "recall@3": recall_at_k(
                retrieved_document_ids,
                case["expected_document_ids"],
                3,
            ),
            "recall@5": recall_at_k(
                retrieved_document_ids,
                case["expected_document_ids"],
                5,
            ),
        }

        chunk_mrr = reciprocal_rank(
            retrieved_chunk_ids,
            case["expected_chunk_ids"],
        )

        document_mrr = reciprocal_rank(
            retrieved_document_ids,
            case["expected_document_ids"],
        )

        filter_acc = filter_accuracy(
            results=results,
            project_id=case.get("project_id"),
            document_type=case.get("document_type"),
        )

        return {
            "case_id": case["id"],
            "query": case["query"],
            "expected_chunk_ids": case["expected_chunk_ids"],
            "expected_document_ids": case["expected_document_ids"],
            "retrieved_chunk_ids": retrieved_chunk_ids,
            "retrieved_document_ids": retrieved_document_ids,
            "project_id": case.get("project_id"),
            "document_type": case.get("document_type"),
            "latency_ms": round(latency_ms, 2),
            "filter_accuracy": filter_acc,
            "chunk_metrics": {
                **chunk_recall,
                "mrr": chunk_mrr,
            },
            "document_metrics": {
                **document_recall,
                "mrr": document_mrr,
            },
            "results": results,
        }
        

    async def run(self) -> list[dict]:
        evaluation_results = []

        for case in self.cases:
            print(f"\n正在评估: {case['id']}")
            print(f"Query: {case['query']}")

            try:
                result = await self.run_case(case)
                evaluation_results.append(result)

            except Exception as exc:
                failure = {
                    "case_id": case["id"],
                    "query": case["query"],
                    "status": "failed",
                    "error_type": type(exc).__name__,
                    "error": str(exc),
                }

                print(
                    f"Evaluation 失败: "
                    f"{case['id']} | "
                    f"{type(exc).__name__}: {exc}"
                )

                evaluation_results.append(failure)

                continue

            print(f"Latency: {result['latency_ms']} ms")
            print(
                "Chunk Metrics: "
                f"R@1={result['chunk_metrics']['recall@1']:.2f}, "
                f"R@3={result['chunk_metrics']['recall@3']:.2f}, "
                f"R@5={result['chunk_metrics']['recall@5']:.2f}, "
                f"MRR={result['chunk_metrics']['mrr']:.2f}"
            )

            print(
                "Document Metrics: "
                f"R@1={result['document_metrics']['recall@1']:.2f}, "
                f"R@3={result['document_metrics']['recall@3']:.2f}, "
                f"R@5={result['document_metrics']['recall@5']:.2f}, "
                f"MRR={result['document_metrics']['mrr']:.2f}"
            )

            print(
                f"Filter Accuracy: "
                f"{result['filter_accuracy']:.2f}"
            )

            print("Retrieved:")

            for item in result["results"]:
                print(
                    f"  chunk={item['chunk_id']} "
                    f"document={item['document_id']} "
                    f"score={item['score']}"
                )

        return evaluation_results

    def summarize(self, results: list[dict]) -> dict:
        if not results:
            return {
                "case_count": 0,
                "successful_case_count": 0,
                "failed_case_count": 0,
                "failures": [],
            }

        successful_results = [
            item
            for item in results
            if item.get("status", "success") == "success"
        ]

        failed_results = [
            item
            for item in results
            if item.get("status") == "failed"
        ]

        if not successful_results:
            return {
                "case_count": len(results),
                "successful_case_count": 0,
                "failed_case_count": len(failed_results),
                "failures": failed_results,
            }

        def average(values: list[float]) -> float:
            return sum(values) / len(values)

        chunk_recall_1 = [
            item["chunk_metrics"]["recall@1"]
            for item in successful_results
        ]

        chunk_recall_3 = [
            item["chunk_metrics"]["recall@3"]
            for item in successful_results
        ]

        chunk_recall_5 = [
            item["chunk_metrics"]["recall@5"]
            for item in successful_results
        ]

        chunk_mrr = [
            item["chunk_metrics"]["mrr"]
            for item in successful_results
        ]

        document_recall_1 = [
            item["document_metrics"]["recall@1"]
            for item in successful_results
        ]

        document_recall_3 = [
            item["document_metrics"]["recall@3"]
            for item in successful_results
        ]

        document_recall_5 = [
            item["document_metrics"]["recall@5"]
            for item in successful_results
        ]

        document_mrr = [
            item["document_metrics"]["mrr"]
            for item in successful_results
        ]

        filter_accuracy = [
            item["filter_accuracy"]
            for item in successful_results
        ]

        latency = [
            item["latency_ms"]
            for item in successful_results
        ]

        return {
            "case_count": len(results),
            "successful_case_count": len(successful_results),
            "failed_case_count": len(failed_results),

            "chunk_metrics": {
                "recall@1": round(
                    average(chunk_recall_1),
                    4,
                ),
                "recall@3": round(
                    average(chunk_recall_3),
                    4,
                ),
                "recall@5": round(
                    average(chunk_recall_5),
                    4,
                ),
                "mrr": round(
                    average(chunk_mrr),
                    4,
                ),
            },

            "document_metrics": {
                "recall@1": round(
                    average(document_recall_1),
                    4,
                ),
                "recall@3": round(
                    average(document_recall_3),
                    4,
                ),
                "recall@5": round(
                    average(document_recall_5),
                    4,
                ),
                "mrr": round(
                    average(document_mrr),
                    4,
                ),
            },

            "filter_accuracy": round(
                average(filter_accuracy),
                4,
            ),

            "latency_ms": {
                "average": round(
                    average(latency),
                    2,
                ),
                "min": round(
                    min(latency),
                    2,
                ),
                "max": round(
                    max(latency),
                    2,
                ),
            },

            "failures": failed_results,
        }

    def save_report(
        self,
        results: list[dict],
        summary: dict,
    ) -> str:
        report = {
            "evaluation": {
                "version": "v1",
                "dataset_version": "v1",
                "retrieval_version": "v1",
                "created_at": datetime.now().isoformat(),
                "case_count": len(results),
            },
            "retrieval_config": {
                "top_k": 5,
                "embedding_model": "text-embedding-v4",
                "embedding_dimension": 1024,
                "vector_store": "Milvus",
                "collection": "mindflow_chunks_v2",
            },
            "data_coverage": {
                "resume": {
                    "status": "available",
                    "document_ids": [8],
                    "case_count": 4,
                },
                "other": {
                    "status": "available",
                    "document_ids": [6],
                    "case_count": 3,
                },
                "jd": {
                    "status": "missing",
                    "reason": "当前数据库中没有真实的 jd 类型 Document",
                },
                "project": {
                    "status": "missing",
                    "reason": "当前数据库中没有真实的 project 类型 Document",
                },
                "code": {
                    "status": "missing",
                    "reason": "当前数据库中没有真实的 code 类型 Document",
                },
            },
            "summary": summary,
            "cases": results,
        }

        report_dir = Path("evaluation_reports")
        report_dir.mkdir(parents=True, exist_ok=True)

        timestamp = datetime.now().strftime(
            "%Y%m%d_%H%M%S"
        )

        report_path = (
            report_dir
            / f"rag_evaluation_{timestamp}.json"
        )

        report_path.write_text(
            json.dumps(
                report,
                ensure_ascii=False,
                indent=2,
            ),
            encoding="utf-8",
        )

        return str(report_path)


async def main():
    runner = EvaluationRunner()

    results = await runner.run()

    summary = runner.summarize(results)

    report_path = runner.save_report(
        results=results,
        summary=summary,
    )

    print(f"\nEvaluation report: {report_path}")

    print(
        f"Successful: "
        f"{summary['successful_case_count']}"
    )

    print(
        f"Failed: "
        f"{summary['failed_case_count']}"
    )

    print("\n")
    print("=" * 50)
    print("RAG Evaluation Summary")
    print("=" * 50)

    print(f"\nCases: {summary['case_count']}")

    print("\nChunk:")
    print(
        f"  Recall@1: {summary['chunk_metrics']['recall@1']:.4f}"
    )
    print(
        f"  Recall@3: {summary['chunk_metrics']['recall@3']:.4f}"
    )
    print(
        f"  Recall@5: {summary['chunk_metrics']['recall@5']:.4f}"
    )
    print(
        f"  MRR:      {summary['chunk_metrics']['mrr']:.4f}"
    )

    print("\nDocument:")
    print(
        f"  Recall@1: "
        f"{summary['document_metrics']['recall@1']:.4f}"
    )
    print(
        f"  Recall@3: "
        f"{summary['document_metrics']['recall@3']:.4f}"
    )
    print(
        f"  Recall@5: "
        f"{summary['document_metrics']['recall@5']:.4f}"
    )
    print(
        f"  MRR:      "
        f"{summary['document_metrics']['mrr']:.4f}"
    )

    print(
        "\nFilter Accuracy: "
        f"{summary['filter_accuracy']:.4f}"
    )

    print("\nLatency:")
    print(
        f"  Average: "
        f"{summary['latency_ms']['average']:.2f} ms"
    )
    print(
        f"  Min:     "
        f"{summary['latency_ms']['min']:.2f} ms"
    )
    print(
        f"  Max:     "
        f"{summary['latency_ms']['max']:.2f} ms"
    )


if __name__ == "__main__":
    asyncio.run(main())