"""校验评估数据集自身的正确性。

**为什么必须有这个脚本**：数据集的错误会伪装成"检索变差了"。
如果 marker 太短、在别的材料里也出现，那么"期望值"本身就是错的 ——
但失败信息会指向检索，排查方向从一开始就错了。

因此校验项固定为四条：

1. **每份材料都产出 ≥6 块** —— 否则 top-5 必然全中，
   Recall@5 恒等于 1，指标对任何改动都不敏感。
2. **marker 在目标材料里能找到** —— 否则数据集与语料不一致。
3. **marker 不跨块** —— 跨块时 chunk 级指标没有明确含义
   （允许跨块，但必须在报告里显式排除）。
4. **marker 长度足够且全局唯一** —— 太短会在别处偶然命中。

用法：uv run python -m app.core.check_eval_dataset
"""

import sys

from app.evaluation.datasets import DOCUMENTS, EVALUATION_CASES
from app.services.chunking_service import ChunkingService

# 每份材料最少应产出的块数。
#
# 取 6 而不是 5：检索取 top-5。若一份材料只有 5 块，
# Recall@5 仍然恒等于 1（全中），指标依然没有区分度。
# 要留出"必然漏掉一些"的空间，才能测出召回差异。
MIN_CHUNKS_PER_DOCUMENT = 6

# 切块大小。校验脚本需要它来推算每块的字符区间。
CHUNK_SIZE = 500

# marker 的最短长度。
#
# 太短的串（例如"知道"）会在多处偶然出现，使"期望值"失去意义。
# 12 是权衡值：中文技术材料里 12 字的连续片段通常已足够特定，
# 而要求更长会逼着人去凑字，反而写出不自然的标注。
MIN_MARKER_LENGTH = 12


def main() -> int:
    failures: list[str] = []
    warnings: list[str] = []

    print("=" * 74)
    print("RAG 评估数据集校验")
    print("=" * 74)

    corpus = {spec.name: spec.content for spec in DOCUMENTS}
    chunk_map: dict[str, list[str]] = {}
    # 每块的字符区间，用于按偏移判断 marker 是否跨块
    segment_ranges: dict[str, list[tuple[int, int]]] = {}

    print()
    print("1. 材料长度与块数")
    print(f"   （要求：每份 ≥{MIN_CHUNKS_PER_DOCUMENT} 块）")

    for spec in DOCUMENTS:
        chunks = ChunkingService.split_text(
            spec.content,
            chunk_size=500,
            chunk_overlap=0,
        )
        chunk_map[spec.name] = chunks

        # 记录每块的字符区间。
        #
        # 重叠为 0 时步长固定，因此区间可直接推算；
        # 用区间判定 marker 是否跨块（见下方说明）。
        segment_ranges[spec.name] = [
            (index * CHUNK_SIZE, min((index + 1) * CHUNK_SIZE, len(spec.content)))
            for index in range(len(chunks))
        ]

        mark = "OK " if len(chunks) >= MIN_CHUNKS_PER_DOCUMENT else "!! "

        print(
            f"   [{mark}] {spec.name:<28} "
            f"{len(spec.content):>5} 字符 → {len(chunks):>3} 块 "
            f"[{spec.document_type}]"
        )

        if len(chunks) < MIN_CHUNKS_PER_DOCUMENT:
            failures.append(
                f"{spec.name} 只产出 {len(chunks)} 块"
                f"（需要 ≥{MIN_CHUNKS_PER_DOCUMENT}）—— "
                f"top-5 会必然全中，Recall@5 失去区分度"
            )

    print()
    print("2. marker 的定位与唯一性")

    for case in EVALUATION_CASES:
        content = corpus.get(case.document_name)

        if content is None:
            failures.append(
                f"{case.id}: 材料 {case.document_name} 不存在"
            )
            continue

        chunks = chunk_map[case.document_name]

        # 2a. 长度
        if len(case.expected_marker) < MIN_MARKER_LENGTH:
            failures.append(
                f"{case.id}: marker 只有 "
                f"{len(case.expected_marker)} 字符"
                f"（要求 ≥{MIN_MARKER_LENGTH}）："
                f"{case.expected_marker!r}"
            )

        # 2b. 在目标材料里能找到，且统计跨了几块。
        #
        # ⚠️ 必须按**字符偏移**判定，不能用"marker 是否是某块的子串"。
        # 后者会漏掉一种情形：**marker 正好被切块边界切开**。
        #
        # 实测例：定位词落在第 497 字符而块大小是 500，
        # 于是块 0 结尾是"…检索质"、块 1 开头是"量评估经验…"。
        # 两块都不含完整串，判定会报"找不到" ——
        # 看起来像切块丢了内容，实际只是串跨了边界。
        # 这种误报会让人去修一个没坏的东西。
        content = corpus.get(case.document_name, "")

        marker_start = content.find(case.expected_marker)

        if marker_start < 0:
            failures.append(
                f"{case.id}: marker 在 {case.document_name} "
                f"里找不到：{case.expected_marker[:40]!r}"
            )
            continue

        marker_end = marker_start + len(case.expected_marker)

        # 重建每块的字符区间（与 ChunkingService 的固定步长一致）
        block_ranges = segment_ranges[case.document_name]

        covered = [
            block_index
            for block_index, (start, end) in enumerate(block_ranges)
            if start <= marker_start and marker_end <= end
        ]

        if not covered:
            # 跨边界：报告它落在哪两块之间，便于定位
            failures.append(
                f"{case.id}: marker 跨越块边界"
                f"（第 {marker_start}–{marker_end} 字符，"
                f"块大小为 {CHUNK_SIZE}）"
            )
            continue

        hit_indexes = covered

        if len(hit_indexes) > 1:
            failures.append(
                f"{case.id}: marker 同时完整落在 "
                f"{len(hit_indexes)} 块内 —— 无法判断命中哪一块算对"
            )

        other_hits = [
            name
            for name, other_chunks in chunk_map.items()
            if name != case.document_name
            and any(
                case.expected_marker in text
                for text in other_chunks
            )
        ]

        if other_hits:
            warnings.append(
                f"{case.id}: marker 也出现在 {other_hits} —— "
                f"不加类型过滤时可能产生假命中"
            )

        status = f"{len(hit_indexes)} 块"

        if len(hit_indexes) > 1:
            status += "（跨块 → 只计入文档级）"
            warnings.append(
                f"{case.id}: marker 跨 {len(hit_indexes)} 块，"
                f"chunk 级指标会被排除"
            )

        print(
            f"   [OK ] {case.id:<32} {status:<28} "
            f"{case.document_type or '（不限类型）'}"
        )

    # ---------------- 汇总 ----------------
    print()
    print("=" * 74)

    by_type: dict[str, int] = {}
    for case in EVALUATION_CASES:
        key = case.document_type or "cross"
        by_type[key] = by_type.get(key, 0) + 1

    print(f"  用例 {len(EVALUATION_CASES)} 个，按类型：{by_type}")
    print(f"  材料 {len(DOCUMENTS)} 份")

    if warnings:
        print()
        print(f"  提示 {len(warnings)} 条：")
        for item in warnings:
            print(f"    - {item}")

    if failures:
        print()
        print(f"  ✗ {len(failures)} 处不合格：")
        for item in failures:
            print(f"    - {item}")
        return 1

    print()
    print("  ✓ 数据集校验通过")
    return 0


if __name__ == "__main__":
    sys.exit(main())
