"""RAG 检索评估 Runner。

## 与旧版的区别

旧版直接用数据集里**硬编码的 chunk / document id**，因此：

- 数据集会随库状态失效（实测：清理后期望的 chunk 1-4 落到了别的文档）
- 期望的素材是垃圾数据（112 字符、1 块的 txt 充当 4 个问题的答案）
- 只有 7 个 case，Recall 分辨率约 14%

本版**自带语料**：评测开始时把 `DOCUMENTS` 播种到一个临时项目，
拿到真实 id，测完清理。因此可重复、可扩展、覆盖多类型。

## 一个诚实的取舍：跨块的期望值不参与 chunk 级指标

`expected_marker` 是一段特征子串。若它跨了两个 chunk，
**chunk 级 Recall 没有明确含义** —— 命中两块才算对？
只命中一块算半个对？任何算法都是在编数字。

因此这类 case 标为 `document_level_only`，**只参与文档级指标**，
并从 chunk 级均值里排除。宁可样本少一点，也不要一个
说不清含义的指标 —— 后者的危害是让人以为改动有效。
"""

import asyncio
import time

import json
from datetime import datetime
from pathlib import Path

from sqlalchemy import select

from app.database.session import AsyncSessionLocal
from app.evaluation.datasets import (
    DOCUMENTS,
    EVALUATION_CASES,
    EvaluationCase,
)
from app.models.document import Document
from app.models.document_chunk import DocumentChunk
from app.models.project import Project
from app.services.chunking_service import ChunkingService
from app.services.embedding_pipeline_service import (
    EmbeddingPipelineService,
)
from app.services.milvus_vector_store import MilvusVectorStore
from app.services.retrieval_service import RetrievalService
from app.evaluation.metrics import (
    filter_accuracy,
    recall_at_k,
    reciprocal_rank,
)

# 评估用的临时项目名。
# 固定前缀便于异常中断后清理残留。
EVALUATION_PROJECT_PREFIX = "__rag_eval__"


class EvaluationRunner:
    """RAG Evaluation Runner（自带语料版）。"""

    def __init__(self):
        self.cases = EVALUATION_CASES

        # 播种后填充：材料名 → (document_id, [chunk_id, ...])
        self.seeded: dict[str, tuple[int, list[int]]] = {}

        self.project_id: int | None = None
        self.owner_id: int | None = None

    # ------------------------------------------------------------ 播种

    async def seed(self) -> None:
        """把数据集语料播种到一个临时项目。"""

        async with AsyncSessionLocal() as db:
            # 找一个用户作为归属。评估不关心是谁的，
            # 但项目必须有 owner（外键约束）。
            from app.models.user import User

            owner = (
                await db.execute(select(User).order_by(User.id).limit(1))
            ).scalars().first()

            if owner is None:
                raise RuntimeError(
                    "库里没有任何用户，无法播种评估语料。"
                    "请先注册一个账号。"
                )

            self.owner_id = owner.id

            project = Project(
                name=f"{EVALUATION_PROJECT_PREFIX}{datetime.now():%Y%m%d_%H%M%S}",
                owner_id=owner.id,
            )
            db.add(project)
            await db.commit()
            await db.refresh(project)

            self.project_id = project.id

            for document_spec in DOCUMENTS:
                chunks = ChunkingService.split_text(
                    document_spec.content,
                    chunk_size=500,
                    chunk_overlap=0,
                )

                document = Document(
                    name=document_spec.name,
                    original_filename=document_spec.name,
                    file_type="md",
                    document_type=document_spec.document_type,
                    project_id=project.id,
                    status="chunked",
                    content=document_spec.content,
                )
                db.add(document)
                await db.commit()
                await db.refresh(document)

                for index, text in enumerate(chunks):
                    db.add(
                        DocumentChunk(
                            document_id=document.id,
                            chunk_index=index,
                            content=text,
                            embedding_status="pending",
                            chunk_metadata={
                                "document_type": document_spec.document_type,
                            },
                        )
                    )

                await db.commit()

                chunk_ids = list(
                    (
                        await db.execute(
                            select(DocumentChunk.id)
                            .where(DocumentChunk.document_id == document.id)
                            .order_by(DocumentChunk.id)
                        )
                    ).scalars().all()
                )

                self.seeded[document_spec.name] = (
                    document.id,
                    chunk_ids,
                )

            print(
                f"  已播种 {len(DOCUMENTS)} 份材料到临时项目 "
                f"{self.project_id}"
            )

        # 嵌入。
        #
        # `embed_document` 自己会 commit，且只处理 `embedding_status
        # == 'pending'` 的 chunk —— 因此这里不需要再手动改文档状态。
        async with AsyncSessionLocal() as db:
            pipeline = EmbeddingPipelineService(db)

            for name, (document_id, _chunk_ids) in self.seeded.items():
                embedded = await pipeline.embed_document(document_id)
                print(f"    {name}: 已嵌入 {embedded} 段")

    async def cleanup(self) -> None:
        """删掉临时项目及其向量。"""

        if self.project_id is None:
            return

        try:
            async with AsyncSessionLocal() as db:
                chunk_ids = list(
                    (
                        await db.execute(
                            select(DocumentChunk.id).where(
                                DocumentChunk.document_id.in_(
                                    select(Document.id).where(
                                        Document.project_id
                                        == self.project_id
                                    )
                                )
                            )
                        )
                    ).scalars().all()
                )

                if chunk_ids:
                    try:
                        await MilvusVectorStore().delete(ids=chunk_ids)
                    except Exception as exc:  # noqa: BLE001
                        print(f"    清理向量失败：{exc}")

                await db.execute(
                    Document.__table__.delete().where(
                        Document.__table__.c.project_id == self.project_id
                    )
                )
                await db.execute(
                    Project.__table__.delete().where(
                        Project.__table__.c.id == self.project_id
                    )
                )
                await db.commit()

            print(f"  已清理临时项目 {self.project_id}")

        except Exception as exc:  # noqa: BLE001
            print(f"  清理失败（需手工处理）：{exc}")

    # ------------------------------------------------------------ 解析期望值

    async def resolve_expectations(
        self,
        case: EvaluationCase,
    ) -> tuple[list[int], bool]:
        """把 `expected_marker` 解析成 chunk id。

        返回 (期望的 chunk id 列表, 是否只适合文档级指标)。

        **为什么要判断"是否跨块"**：marker 若跨了两个 chunk，
        chunk 级 Recall 就没有明确含义 —— 命中两块才叫对？
        只命中一块算半个对？任何算法都是在编数字。
        这种情况返回 `document_level_only=True`，
        调用方把它排除在 chunk 级均值之外。
        """

        document_id, chunk_ids = self.seeded[case.document_name]

        async with AsyncSessionLocal() as db:
            rows = list(
                (
                    await db.execute(
                        select(DocumentChunk.id, DocumentChunk.content)
                        .where(DocumentChunk.id.in_(chunk_ids))
                        .order_by(DocumentChunk.id)
                    )
                ).all()
            )

        matching = [
            row[0]
            for row in rows
            if case.expected_marker in (row[1] or "")
        ]

        if not matching:
            raise RuntimeError(
                f"用例 {case.id} 的 marker 在材料 "
                f"{case.document_name} 的任何 chunk 里都找不到："
                f"{case.expected_marker[:40]!r}。"
                "数据集已与语料不一致 —— 必须先修数据集，"
                "否则测出来的是假指标。"
            )

        return matching, len(matching) > 1

    # ------------------------------------------------------------ 单例

    async def run_case(self, case: EvaluationCase) -> dict:
        expected_chunk_ids, document_only = (
            await self.resolve_expectations(case)
        )

        document_id, _chunk_ids = self.seeded[case.document_name]

        start_time = time.perf_counter()

        async with AsyncSessionLocal() as db:
            results = await RetrievalService(db).retrieve(
                query=case.query,
                limit=5,
                project_id=self.project_id,
                document_type=case.document_type,
            )

        latency_ms = (time.perf_counter() - start_time) * 1000

        retrieved_chunk_ids = [item["chunk_id"] for item in results]
        retrieved_document_ids = [item["document_id"] for item in results]

        expected_document_ids = [document_id]

        chunk_metrics = {
            f"recall@{k}": recall_at_k(
                retrieved_chunk_ids, expected_chunk_ids, k
            )
            for k in (1, 3, 5)
        }
        chunk_metrics["mrr"] = reciprocal_rank(
            retrieved_chunk_ids, expected_chunk_ids
        )

        document_metrics = {
            f"recall@{k}": recall_at_k(
                retrieved_document_ids, expected_document_ids, k
            )
            for k in (1, 3, 5)
        }
        document_metrics["mrr"] = reciprocal_rank(
            retrieved_document_ids, expected_document_ids
        )

        return {
            "case_id": case.id,
            "query": case.query,
            "document_name": case.document_name,
            "difficulty": case.difficulty,
            "expected_chunk_ids": expected_chunk_ids,
            "expected_document_ids": expected_document_ids,
            "retrieved_chunk_ids": retrieved_chunk_ids,
            "retrieved_document_ids": retrieved_document_ids,
            "project_id": self.project_id,
            "document_type": case.document_type,
            "latency_ms": round(latency_ms, 2),
            "filter_accuracy": filter_accuracy(
                results=results,
                project_id=self.project_id,
                document_type=case.document_type,
            ),
            "document_level_only": document_only,
            "chunk_metrics": chunk_metrics,
            "document_metrics": document_metrics,
            "results": results,
        }

    # ------------------------------------------------------------ 全部

    async def run(self) -> list[dict]:
        evaluation_results = []

        for case in self.cases:
            print(f"\n正在评估: {case.id}")
            print(f"  Query: {case.query}")

            try:
                result = await self.run_case(case)
                evaluation_results.append(result)

            except Exception as exc:  # noqa: BLE001
                evaluation_results.append(
                    {
                        "case_id": case.id,
                        "query": case.query,
                        "status": "failed",
                        "error_type": type(exc).__name__,
                        "error": str(exc),
                    }
                )
                print(
                    f"  失败：{type(exc).__name__}: {exc}"
                )
                continue

            marker = (
                "（跨块，只计入文档级）"
                if result["document_level_only"]
                else ""
            )
            print(
                f"  Chunk R@1={result['chunk_metrics']['recall@1']:.2f} "
                f"R@3={result['chunk_metrics']['recall@3']:.2f} "
                f"R@5={result['chunk_metrics']['recall@5']:.2f} "
                f"MRR={result['chunk_metrics']['mrr']:.2f} {marker}"
            )
            print(
                f"  Doc   R@1={result['document_metrics']['recall@1']:.2f} "
                f"R@3={result['document_metrics']['recall@3']:.2f} "
                f"R@5={result['document_metrics']['recall@5']:.2f} "
                f"MRR={result['document_metrics']['mrr']:.2f}"
            )
            print(
                f"  过滤正确率={result['filter_accuracy']:.2f} "
                f"延迟={result['latency_ms']:.0f}ms"
            )

        return evaluation_results

    # ------------------------------------------------------------ 汇总

    def summarize(self, results: list[dict]) -> dict:
        successful = [
            item
            for item in results
            if item.get("status", "success") == "success"
        ]
        failed = [
            item for item in results if item.get("status") == "failed"
        ]

        # chunk 级均值**排除跨块用例** —— 它们的 chunk 级指标
        # 没有明确含义（见 `resolve_expectations` 的说明）。
        chunk_eligible = [
            item
            for item in successful
            if not item.get("document_level_only")
        ]

        def average(values: list[float]) -> float:
            return sum(values) / len(values) if values else 0.0

        def extract(items, scope, key):
            return [item[scope][key] for item in items]

        summary = {
            "case_count": len(results),
            "successful_case_count": len(successful),
            "failed_case_count": len(failed),
            "chunk_metric_case_count": len(chunk_eligible),
            "document_level_only_case_count": (
                len(successful) - len(chunk_eligible)
            ),
            "failures": failed,
        }

        if not successful:
            return summary

        # 文档级：全部成功用例
        summary["document_metrics"] = {
            "recall@1": average(extract(successful, "document_metrics", "recall@1")),
            "recall@3": average(extract(successful, "document_metrics", "recall@3")),
            "recall@5": average(extract(successful, "document_metrics", "recall@5")),
            "mrr": average(extract(successful, "document_metrics", "mrr")),
        }

        # chunk 级：排除跨块用例
        if chunk_eligible:
            summary["chunk_metrics"] = {
                "recall@1": average(extract(chunk_eligible, "chunk_metrics", "recall@1")),
                "recall@3": average(extract(chunk_eligible, "chunk_metrics", "recall@3")),
                "recall@5": average(extract(chunk_eligible, "chunk_metrics", "recall@5")),
                "mrr": average(extract(chunk_eligible, "chunk_metrics", "mrr")),
            }
        else:
            summary["chunk_metrics"] = None

        summary["filter_accuracy"] = average(
            [item["filter_accuracy"] for item in successful]
        )
        summary["avg_latency_ms"] = average(
            [item["latency_ms"] for item in successful]
        )

        # 按难度与类型分组，便于看短板在哪
        for field in ("document_type", "difficulty"):
            grouped: dict[str, list[float]] = {}
            for item in successful:
                key = str(item.get(field))
                grouped.setdefault(key, []).append(
                    item["document_metrics"]["recall@5"]
                )
            summary[f"document_recall@5_by_{field}"] = {
                key: round(average(values), 3)
                for key, values in grouped.items()
            }

        return summary

    # ------------------------------------------------------------ 落盘

    def save_report(self, results: list[dict], summary: dict) -> str:
        report = {
            "evaluation": {
                "version": "v2",
                "dataset_version": "v2",
                "retrieval_version": "v1",
                "created_at": datetime.now().isoformat(),
                "case_count": len(results),
                "corpus_seeded": True,
                "corpus_documents": [
                    {
                        "name": spec.name,
                        "document_type": spec.document_type,
                        "chars": len(spec.content),
                    }
                    for spec in DOCUMENTS
                ],
            },
            "retrieval_config": {
                "top_k": 5,
                "embedding_model": "text-embedding-v4",
                "embedding_dimension": 1024,
                "vector_store": "Milvus",
                "collection": MilvusVectorStore.COLLECTION_NAME,
                "metric_type": "COSINE",
                "consistency_level": "Strong",
                # 明确记录"检索是单路稠密"，避免报告被误读为混合检索
                "retrieval_mode": "dense_only",
                "rerank": False,
                "score_normalization": None,
            },
            "chunking": {
                "chunk_size": 500,
                "chunk_overlap": 100,
            },
            "summary": summary,
            "cases": [
                {
                    key: value
                    for key, value in item.items()
                    if key != "results"
                }
                for item in results
            ],
        }

        output_dir = Path(__file__).resolve().parents[2] / "evaluation_reports"
        output_dir.mkdir(parents=True, exist_ok=True)

        path = output_dir / (
            f"rag_evaluation_{datetime.now():%Y%m%d_%H%M%S}.json"
        )
        path.write_text(
            json.dumps(report, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

        return str(path)


async def main() -> int:
    runner = EvaluationRunner()

    print("=" * 74)
    print("RAG 检索评估（自带语料）")
    print("=" * 74)

    try:
        await runner.seed()

        results = await runner.run()
        summary = runner.summarize(results)

        print()
        print("=" * 74)
        print("汇总")
        print("=" * 74)
        print(
            f"  用例：{summary['case_count']} 个"
            f"（成功 {summary['successful_case_count']}，"
            f"失败 {summary['failed_case_count']}）"
        )
        print(
            f"  文档级指标覆盖 {summary['successful_case_count']} 个用例；"
            f"chunk 级覆盖 {summary['chunk_metric_case_count']} 个"
            f"（排除跨块 {summary['document_level_only_case_count']} 个）"
        )

        document_metrics = summary.get("document_metrics")

        if document_metrics:
            print()
            print("  **文档级**（Recall@k = top-k 命中正确文档的比例）")
            print(
                f"    R@1={document_metrics['recall@1']:.3f}  "
                f"R@3={document_metrics['recall@3']:.3f}  "
                f"R@5={document_metrics['recall@5']:.3f}  "
                f"MRR={document_metrics['mrr']:.3f}"
            )

        chunk_metrics = summary.get("chunk_metrics")

        if chunk_metrics:
            print()
            print("  **chunk 级**（R@k = top-k 命中目标块的比例）")
            print(
                f"    R@1={chunk_metrics['recall@1']:.3f}  "
                f"R@3={chunk_metrics['recall@3']:.3f}  "
                f"R@5={chunk_metrics['recall@5']:.3f}  "
                f"MRR={chunk_metrics['mrr']:.3f}"
            )

        print()
        print(f"  过滤正确率：{summary['filter_accuracy']:.3f}")
        print(f"  平均延迟：{summary['avg_latency_ms']:.0f}ms")

        for key in (
            "document_recall@5_by_document_type",
            "document_recall@5_by_difficulty",
        ):
            if key in summary:
                print(f"  {key}: {summary[key]}")

        path = runner.save_report(results, summary)
        print()
        print(f"  报告已写入：{path}")

        if summary["failed_case_count"]:
            print()
            print("  ⚠️ 有失败用例，上面的均值只覆盖成功用例")
            for item in summary["failures"]:
                print(f"    - {item['case_id']}: {item.get('error', '')[:80]}")

        return 0

    finally:
        await runner.cleanup()


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
