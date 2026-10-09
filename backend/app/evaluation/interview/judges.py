"""Interview Engine 评估的规则判定层。

这里只做"确定性判定"，不调用 LLM。
判定输入来自：
- 数据集里的 expected_missing_points / forbidden_missing_points
  / expected_follow_up_keywords
- 实际运行 InterviewAnswerAnalyzer 和 InterviewFollowUpGenerator
  得到的 missing_points 与 follow_up_question

匹配规则：

1. 归一化：转小写、全角转半角、下划线与连字符统一为空格。
   因此 chunk_id / chunk id / chunk-id 会被视为同一个概念。
2. 英文关键词按 token 序列匹配：
   - 单 token 关键词（metadata、rerank）要求 token 完全相等，
     避免 "id" 命中 "consider" 这类子串误判。
   - 多 token 关键词（chunk id、top k）按相邻 token 序列匹配。
3. 中文关键词使用子串匹配，并通过 ALIASES 别名表覆盖
   LLM 常见的不同措辞。

注意：不要往数据集里写 "id" 这种过短的关键词。
它在 token 匹配下几乎不会误判，但语义太宽，
任何回答都可能被算成命中，失去评估意义。
"""

import re
import unicodedata


# 关键词别名表。
# key 是数据集里写的关键词（归一化后），
# value 是判定时额外接受的其他写法。
ALIASES: dict[str, tuple[str, ...]] = {
    # ---- chunk_id ----
    "chunk id": (
        "chunkid",
        "chunk 编号",
        "chunk 主键",
        "块 id",
    ),
    # ---- metadata ----
    "metadata": (
        "元数据",
        "meta 字段",
        "附加字段",
        "业务字段",
    ),
    # ---- 向量表关联 ----
    "向量表关联": (
        "关联字段",
        "关联关系",
        "映射关系",
        "对应关系",
        "如何对应",
        "如何关联",
        "外键",
        "关联",
        "标识",
        "唯一标识",
        "对应",
        "映射",
    ),
    # ---- top-k ----
    # 注意：key 必须使用归一化后的写法（空格），
    # 因为 keyword_hits 是拿归一化后的关键词来查表的。
    "top k": (
        "top k",
        "topk",
        "top k 结果",
        "召回数量",
        "召回条数",
        "返回数量",
        "截断",
        "数量控制",
        "取几条",
    ),
    # ---- 排序 ----
    "排序": (
        "重排",
        "rerank",
        "re-rank",
        "相关性",
        "相似度",
        "score",
        "打分",
        "顺序",
    ),
    # ---- 过滤 ----
    "过滤": (
        "筛选",
        "过滤条件",
        "filter",
        "project id",
        "document type",
        "按项目",
        "按类型",
        "权限",
    ),
    # ---- 分块相关 ----
    # 实测模型经常把"切分"写成 "chunk"，例如
    # "未说明 chunk 长度的具体取值" 与 "未说明切分长度" 是同一件事。
    # 中英混用是常态，别名表必须覆盖这类等价写法，
    # 否则 R1 会把正确识别出的缺口判成"未命中"。
    "切分长度": (
        "chunk 长度",
        "分块长度",
        "块长度",
        "chunk size",
        "切分粒度",
        "chunk 粒度",
        "分块粒度",
        "chunk长度",
    ),
    "切分依据": (
        "chunk 依据",
        "分块依据",
        "切分标准",
        "调整依据",
        "决策标准",
        "为什么选择",
        "选择该长度",
        "选择这个长度",
        "调优依据",
        "参数依据",
    ),
    "切分策略": (
        "chunk 策略",
        "chunk 切分",
        "分块策略",
        "切分方法",
        "分块方法",
        "切分规则",
        "切分方式",
        "chunk 切分",
    ),
    "切分": (
        "chunk",
        "分块",
        "切块",
    ),
}

# 判定为"泛泛提问"的空话模式。
VAGUE_FOLLOW_UP_PATTERNS: tuple[str, ...] = (
    "请详细介绍一下",
    "请详细介绍",
    "请具体介绍一下",
    "请展开说说",
    "请展开介绍",
    "详细说明一下你的项目",
    "介绍一下你",
    "说说你的理解",
    "还有什么补充",
)

# 全角转半角映射表。
_FULLWIDTH_TO_HALFWIDTH = str.maketrans(
    "０１２３４５６７８９ＡＢＣＤＥＦＧＨＩＪＫＬＭＮＯＰＱＲＳＴＵＶＷＸＹＺ"
    "ａｂｃｄｅｆｇｈｉｊｋｌｍｎｏｐｑｒｓｔｕｖｗｘｙｚ－＿",
    "0123456789ABCDEFGHIJKLMNOPQRSTUVWXYZ"
    "abcdefghijklmnopqrstuvwxyz-_",
)


def normalize(text: str) -> str:
    """归一化文本，用于关键词匹配。

    - 全角转半角
    - 下划线、连字符、顿号分隔统一为空格
    - 转小写并压缩连续空白
    """

    if not text:
        return ""

    text = unicodedata.normalize("NFKC", text)
    text = text.translate(_FULLWIDTH_TO_HALFWIDTH)
    text = re.sub(r"[_\-/]+", " ", text)
    text = text.lower()
    text = re.sub(r"\s+", " ", text)

    return text.strip()


def _tokenize(text: str) -> list[str]:
    """把归一化文本切成 token 序列。

    英文与数字按连续字符切分，中文按字符切分，
    这样 "chunk id 设计" → ["chunk", "id", "设计"]。
    """

    return re.findall(r"[a-z0-9]+|[\u4e00-\u9fff]", text)


def _contains(haystack: str, needle: str) -> bool:
    """单个写法是否出现在已归一化的文本中。

    中文用子串匹配；
    英文按 token 序列匹配，单 token 要求完全相等，
    多 token 要求相邻出现。
    """

    if not needle:
        return False

    if re.search(r"[\u4e00-\u9fff]", needle):
        # 中文关键词可能混排英文，例如 "向量表关联"，
        # 直接做子串匹配即可。
        return needle in haystack

    needle_tokens = _tokenize(needle)
    if not needle_tokens:
        return False

    haystack_tokens = _tokenize(haystack)
    size = len(needle_tokens)

    for start in range(len(haystack_tokens) - size + 1):
        if haystack_tokens[start:start + size] == needle_tokens:
            return True

    return False


def keyword_hits(
    text: str,
    keywords: list[str],
    candidates: dict[str, tuple[str, ...]] | None = None,
) -> dict[str, bool]:
    """返回每个关键词是否命中。

    命中判定为：关键词本身，或其任一候选写法出现。

    candidates 用于测试或临时覆盖关键词写法；
    默认从 ALIASES 取，取不到就只用关键词自身。
    """

    haystack = normalize(text)
    candidates = candidates or {}
    result: dict[str, bool] = {}

    for keyword in keywords:
        needle = normalize(keyword)

        if keyword in candidates:
            extra = candidates[keyword]
        else:
            extra = ALIASES.get(needle, ())

        needles = [needle, *(normalize(k) for k in extra)]

        result[keyword] = any(
            _contains(haystack, candidate)
            for candidate in needles
            if candidate
        )

    return result


def normalized_join(items: list[str]) -> str:
    """把多条文本合成一段用于匹配。

    注意：这里保留原文，
    由 keyword_hits 内部做归一化处理。
    """

    return "\n".join(item for item in items if item)


def _hit_any(
    text: str,
    keywords: list[str],
) -> tuple[bool, list[str]]:
    """keywords 中是否有任意一个命中，并返回命中列表。"""

    hits = keyword_hits(text=text, keywords=keywords)
    matched = [key for key, hit in hits.items() if hit]

    return bool(matched), matched


def evaluate_missing_point_coverage(
    missing_points: list[str],
    expected: list[str],
) -> dict:
    """R1：分析出的缺口是否命中期望缺口。"""

    joined = normalized_join(missing_points)
    hits = keyword_hits(text=joined, keywords=expected)
    matched = [key for key, hit in hits.items() if hit]
    missed = [key for key, hit in hits.items() if not hit]

    return {
        "passed": bool(matched) if expected else True,
        "expected": expected,
        "matched": matched,
        "missed": missed,
        "hit_rate": (
            len(matched) / len(expected)
            if expected
            else 1.0
        ),
    }


def evaluate_forbidden_missing_points(
    missing_points: list[str],
    forbidden: list[str],
) -> dict:
    """R2（已废弃）：是否把不该报的点报成了缺失。

    ⚠️ **本规则已废弃，不要在新数据集中使用 `forbidden_missing_points`。**

    废弃原因（三次实测误报，见 MIND_FLOW_PLAN.md §8A.7 D4）：

    1. 判据在原理上无法成立。
       任何"未说明 X"模式都能被正当地用来批"X 讲得不够深"。
       实测模型输出的合理缺口包括：
         - "未说明简历解析的**具体实现**，例如支持格式、解析工具…"
         - "未说明 500 字符分块和 100 字符 overlap 的**参数依据**"
       这两条都命中了"整条链路缺失"级别的模式，但它们其实是正确的批评。
       要区分"整条链路没讲"与"讲了但不够深"，只能靠语义理解。

    2. 本规则是冗余的。
       它要防的失败模式是"分析器冤枉完整回答"，
       而 R4（`evaluate_analyzer_score_floor`）已经覆盖：
       若分析器真的冤枉完整回答，三项评分会掉到门槛以下，R4 直接失败。
       实测印证：完整回答报出 10 条缺口，但 `answer_quality = 90`，
       说明 **missing_points 的详略与评分高低是解耦的**，不应据此判违规。

    函数保留仅为兼容旧报告与旧数据集，返回值不再参与通过判定。
    """

    joined = normalized_join(missing_points)
    hits = keyword_hits(text=joined, keywords=forbidden)
    violated = [key for key, hit in hits.items() if hit]
    deprecated_note = (
        "R2 已废弃：任何『未说明 X』模式都可能命中合理缺口，"
        "请改用 R4 判断是否冤枉完整回答。"
    )

    return {
        "passed": not violated,
        "deprecated": True,
        "deprecated_note": deprecated_note,
        "forbidden": forbidden,
        "violated": violated,
    }


def evaluate_follow_up_targeting(
    follow_up_question: str,
    expected_keywords: list[str],
) -> dict:
    """R3：追问是否命中了期望的技术点，且没有退化成泛泛提问。"""

    hits = keyword_hits(
        text=follow_up_question,
        keywords=expected_keywords,
    )
    matched = [key for key, hit in hits.items() if hit]

    normalized_question = normalize(follow_up_question)
    vague_patterns = [
        pattern
        for pattern in VAGUE_FOLLOW_UP_PATTERNS
        if normalize(pattern) in normalized_question
    ]

    targeted = bool(matched) if expected_keywords else True

    return {
        "passed": targeted and not vague_patterns,
        "expected_keywords": expected_keywords,
        "matched": matched,
        "missed": [
            key for key, hit in hits.items() if not hit
        ],
        "hit_rate": (
            len(matched) / len(expected_keywords)
            if expected_keywords
            else 1.0
        ),
        "vague_patterns": vague_patterns,
        "is_vague": bool(vague_patterns),
    }


def evaluate_analyzer_score_floor(
    answer_quality: int,
    technical_depth: int,
    completeness: int,
    min_score: int,
) -> dict:
    """校验回答分析的三项评分是否达到该轮应有的门槛。

    用于完整回答不被低分冤枉：
    如果候选人已经把链路讲清楚了，三项评分却很低，
    说明分析器在凭模板扣分。
    """

    scores = {
        "answer_quality": answer_quality,
        "technical_depth": technical_depth,
        "completeness": completeness,
    }

    below = {
        name: value
        for name, value in scores.items()
        if value < min_score
    }

    return {
        "passed": not below,
        "min_score": min_score,
        "scores": scores,
        "below_min_score": below,
    }


# ============================================================
# 自测：不调用 LLM，验证匹配规则本身是否正确
# ============================================================


_SELF_TEST_CASES = [
    # (说明, 待匹配文本, 关键词, 候选写法, 期望是否命中)
    # 候选写法为 None 时使用 ALIASES 默认别名。

    # ---- 英文写法归一化 ----
    ("下划线写法", "chunk_id 与文本表的映射关系缺失", "chunk_id", None, True),
    ("空格写法", "Chunk ID 的设计没有说明", "chunk_id", None, True),
    ("连写写法", "缺少 ChunkID 关联", "chunk_id", None, True),
    ("别名 chunk 编号", "没有说明 chunk 编号如何生成", "chunk_id", None, True),

    # ---- token 匹配精度 ----
    ("不误判子串", "considerations are missing", "chunk_id", None, False),
    ("token 完全相等", "metadatas 字段缺失", "metadata", ("metadata",), False),

    # ---- top-k 的两种真实写法 ----
    ("top k 空格", "没有说明 top k 的截断策略", "top-k", None, True),
    ("TopK 连写", "TopK 参数没有提到", "top-k", None, True),

    # ---- 中文匹配与别名 ----
    ("中文别名关联", "缺少向量表与文本表的关联字段", "向量表关联", None, True),
    ("中文不误判", "Milvus 中存了向量", "向量表关联", None, False),
    ("中文子串命中", "没有说明 Milvus 与 PostgreSQL 如何关联", "向量表关联", None, True),
    ("英文直接命中", "没有说明 metadata 字段", "metadata", None, True),
    ("别名元数据", "缺少元数据设计", "metadata", None, True),
    ("中文直接命中", "没有提到排序策略", "排序", None, True),
    ("别名 rerank", "缺少 rerank 环节", "排序", None, True),

    # ---- 反例：该报的没报 ----
    ("空缺口不命中", "回答完整，没有遗漏", "chunk_id", None, False),
]


def token_set(text: str) -> set[str]:
    """把归一化文本切成 token 集合。

    用于近似去重（同义表述聚类）：
    两条文本共享的 token 越多，越可能是同一件事的不同说法。
    """

    return set(_tokenize(normalize(text)))


def _bigrams(text: str) -> set[str]:
    """生成字符 bigram 集合，用于近似重复判定。

    中文按单字比较太弱（"未说明"这类套话会拉高一切相似度），
    按 bigram 比较能更好地反映"说的是不是同一件事"。
    英文与数字先折叠为单个占位符，避免把词尾切碎。
    """

    normalized = normalize(text)
    normalized = re.sub(r"[a-z0-9]+", "X", normalized)
    compact = re.sub(r"\s+", "", normalized)

    if len(compact) < 2:
        return {compact} if compact else set()

    return {
        compact[index:index + 2]
        for index in range(len(compact) - 1)
    }


def dedupe_paraphrases(
    groups: list[list[str]],
    similarity_threshold: float = 0.35,
    containment_threshold: float = 0.75,
    max_items: int = 6,
) -> list[str]:
    """跨多次采样归并同义表述，去掉重复。

    为什么需要它：
    分析器会采样多次，若把各次采样的 missing_points 直接取并集，
    同一件事的多个说法会同时出现。实测 3 样本 × 6 条 = 18 条，
    而真实主题只有 6 个，报告无法阅读。

    ## 能力边界（重要，不要高估这个函数）

    它做的是**词面近似去重**，不是语义去重。实测标定结果：

    同一份真实数据（18 条，人工判定真实主题 6 个）：

    | similarity_threshold | 去重后条数 |
    | --- | --- |
    | 0.30 | 8 |
    | **0.35（当前值）** | **9** |
    | 0.55 | 13 |
    | 0.60 | 13 |

    **阈值低到 0.30 也只能降到 8 条，达不到真实的 6 个主题。**
    原因是中文同义复述用词差异大：
    "chunk 长度的具体取值及选择该长度的依据" 与
    "选择该长度的理由或依据（如模型上下文限制、检索精度…）"
    指向同一件事，但共享的字符 bigram 很少。

    要真正做语义去重需要引入 embedding 做向量聚类，
    那是另一个架构决定，不在当前范围内。
    因此这里的目标是"把 18 条压到可读的个位数"，
    **不是"精确还原主题数"**。

    `containment_threshold`（包含抑制）用于"扩写型重复"
    （"未说明 X 依据" → "未说明 X 依据与取舍理由"）。
    实测该项在当前数据上几乎不触发，保留是为了覆盖该形态。

    `max_items = 6` 与提示词里"最多 6 条"的要求对齐，
    因此它**不会掩盖模型的真实输出**。

    最后按"被多少次采样提到"排序，多次提到的缺口更可信。
    """

    entries: list[dict] = []

    for group in groups:
        for text in group:
            stripped = text.strip()
            if not stripped:
                continue

            for entry in entries:
                if entry["text"] == stripped:
                    entry["count"] += 1
                    break
            else:
                entries.append(
                    {
                        "text": stripped,
                        "count": 1,
                        "bigrams": _bigrams(stripped),
                    }
                )

    # ---------------- 第 1 阶段：相似度聚类 ----------------

    clusters: list[dict] = []

    for entry in entries:
        best_index = None
        best_score = 0.0

        for index, cluster in enumerate(clusters):
            size_sum = (
                len(entry["bigrams"]) + len(cluster["bigrams"])
            )
            if not size_sum:
                continue

            score = (
                2
                * len(entry["bigrams"] & cluster["bigrams"])
                / size_sum
            )

            if score > best_score:
                best_score = score
                best_index = index

        if best_index is not None and best_score >= similarity_threshold:
            cluster = clusters[best_index]
            cluster["count"] += entry["count"]
            cluster["bigrams"] |= entry["bigrams"]
            if len(entry["text"]) > len(cluster["text"]):
                cluster["text"] = entry["text"]
        else:
            clusters.append(
                {
                    "text": entry["text"],
                    "count": entry["count"],
                    "bigrams": set(entry["bigrams"]),
                }
            )

    # ---------------- 第 2 阶段：包含抑制 ----------------

    # 长的先处理，保证"长条目抑制短条目"的方向
    clusters.sort(key=lambda item: len(item["text"]), reverse=True)

    kept: list[dict] = []

    for cluster in clusters:
        coverage = 0.0

        for holder in kept:
            if not cluster["bigrams"]:
                break

            coverage = max(
                coverage,
                len(cluster["bigrams"] & holder["bigrams"])
                / len(cluster["bigrams"]),
            )

        if coverage >= containment_threshold:
            # 已被更完整的条目覆盖：并入其提到次数后丢弃
            for holder in kept:
                if (
                    len(cluster["bigrams"] & holder["bigrams"])
                    / max(len(cluster["bigrams"]), 1)
                    >= containment_threshold
                ):
                    holder["count"] += cluster["count"]
                    break
            continue

        kept.append(cluster)

    # ---------------- 排序与截断 ----------------

    kept.sort(
        key=lambda item: (item["count"], len(item["text"])),
        reverse=True,
    )

    return [item["text"] for item in kept[:max_items]]


def count_point_mentions(
    groups: list[list[str]],
) -> dict[str, int]:
    """统计每个条目被多少次采样提到（同一次采样内不重复计）。

    用于区分"稳定出现的技术缺口"与"某一次采样的偶然联想"。
    """

    counts: dict[str, int] = {}

    for group in groups:
        seen_in_group: set[str] = set()

        for text in group:
            stripped = text.strip()
            if not stripped or stripped in seen_in_group:
                continue

            seen_in_group.add(stripped)
            counts[stripped] = counts.get(stripped, 0) + 1

    return counts


def _run_dataset_consistency_test() -> list[str]:
    """检查数据集与判定规则是否自相矛盾。

    重点防止两类写错数据的情况：

    1. 同一轮的 expected_missing_points 与
       forbidden_missing_points 指向同一个概念。
       那这一轮无论 LLM 怎么答都必然失败。
    2. 同一轮里出现互相包含的关键词，例如同时写
       "chunk" 和 "chunk_id"，命中会重复计数。
    """

    from importlib.util import module_from_spec, spec_from_file_location
    from pathlib import Path

    dataset_path = Path(__file__).with_name("datasets.py")
    spec = spec_from_file_location("interview_datasets", dataset_path)
    module = module_from_spec(spec)
    spec.loader.exec_module(module)

    problems: list[str] = []

    for case in module.INTERVIEW_EVALUATION_CASES:
        for index, turn in enumerate(case["turns"], start=1):
            tag = f"{case['id']} turn {index}"

            expected = turn.get("expected_missing_points", [])
            forbidden = turn.get("forbidden_missing_points", [])

            expected_hits = keyword_hits(
                text="\n".join(expected),
                keywords=forbidden,
            )

            for keyword, hit in expected_hits.items():
                if hit:
                    problems.append(
                        f"{tag}: forbidden 关键词 {keyword!r} "
                        f"与 expected_missing_points 冲突"
                    )

            # 检查 expected 内部是否存在包含关系
            for outer in expected:
                for inner in expected:
                    if outer == inner:
                        continue
                    if _contains(normalize(outer), normalize(inner)):
                        problems.append(
                            f"{tag}: expected 关键词 {inner!r} "
                            f"被 {outer!r} 包含，会重复计数"
                        )

    return problems


def _run_self_test() -> None:
    failures = []

    for note, text, keyword, candidates, expected in _SELF_TEST_CASES:
        actual = keyword_hits(
            text=text,
            keywords=[keyword],
            candidates=(
                {keyword: candidates}
                if candidates is not None
                else None
            ),
        )[keyword]

        status = "OK  " if actual == expected else "FAIL"

        if actual != expected:
            failures.append(
                f"{note} | 关键词={keyword} | "
                f"期望={expected} | 实际={actual}"
            )

        print(
            f"[{status}] {note:<16} 关键词={keyword:<10} "
            f"期望={expected!s:<5} 实际={actual}"
        )

    print()

    if failures:
        print(f"自测失败 {len(failures)} 项：")
        for item in failures:
            print(f"  - {item}")
        return

    print(f"自测全部通过（{len(_SELF_TEST_CASES)} 项）")


if __name__ == "__main__":
    _run_self_test()

    print()
    print("=" * 50)
    print("数据集一致性检查")
    print("=" * 50)

    problems = _run_dataset_consistency_test()

    if problems:
        print(f"发现 {len(problems)} 个问题：")
        for item in problems:
            print(f"  - {item}")
    else:
        print("通过：expected 与 forbidden 无冲突，expected 内部无相互包含")
