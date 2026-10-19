"""验证评估报告的元信息与实际行为一致（D70）。

## 它防的是一个**已经发生过两次**的错误

| 次数 | 报告里写的 | 实际用的 |
| --- | --- | --- |
| D63 | `text-embedding-v4` | `text-embedding-v3` |
| **D70** | `chunk_overlap: 100` | **`0`** |

两次的根因相同：**报告里的值是手抄的，而不是从执行处读的。**

而后果很具体：报告的全部价值就是"记录了什么配置"。
两个配置差一个变量时，人（或 AI）会基于报告做对照 ——
而报告里那个变量根本没被记录，或者**记的是错的**。

## 判据：测**实际行为**，不是测"常量等于常量"

⚠️ 这一点很重要。若测试只断言
"`EVAL_CHUNK_OVERLAP == report["chunking"]["chunk_overlap"]`"，
那它只能发现"两处赋值不同"，
**发现不了**"两处都写了 100 而真正调用时传的是 0"。

因此本测试：

1. **真的调用** `ChunkingService.split_text`（用报告声称的参数）
2. **从产出里量出**实际的块长与重叠
3. 断言量出来的结果与报告声称的一致

这样才对得起"报告说的与实际做的一致"这句话。

用法：uv run python -m tests.test_eval_report_fidelity
"""

import json
import sys
from pathlib import Path

from app.services.chunking_service import ChunkingService


class Result:
    def __init__(self) -> None:
        self.rows: list[tuple[str, bool, str]] = []

    def check(self, label: str, ok: bool, detail: str = "") -> None:
        self.rows.append((label, ok, detail))
        mark = "OK  " if ok else "FAIL"
        suffix = f"  {detail}" if detail else ""
        print(f"  [{mark}] {label}{suffix}")

    @property
    def failed(self) -> list[str]:
        return [label for label, ok, _ in self.rows if not ok]


def measure_overlap(chunks: list[str]) -> int:
    """从切块结果里**量出**实际重叠字符数。

    做法：取相邻两块，找"前一块的后缀"与"后一块的前缀"的
    最长公共长度 —— 那就是重叠。

    不去读 `split_text` 的参数：那样只能验证"参数被传对了"，
    而我们要验证的是**产出的实际形态**。
    """

    if len(chunks) < 2:
        return 0

    first, second = chunks[0], chunks[1]

    # 最长后缀-前缀匹配
    limit = min(len(first), len(second))
    best = 0

    for size in range(limit, 0, -1):
        if first[-size:] == second[:size]:
            best = size
            break

    return best


def main() -> int:
    sys.path.insert(0, r"D:\yuxi\MindFlow\backend")

    from app.evaluation.runner import (
        EVAL_CHUNK_OVERLAP,
        EVAL_CHUNK_SIZE,
        EVAL_RETRIEVAL_LIMIT,
        EvaluationRunner,
    )

    result = Result()

    print("=" * 78)
    print("评估报告元信息保真度验证")
    print("=" * 78)

    # ================================================
    print()
    print("=" * 78)
    print("① 从**实际切块行为**量出参数，并与常量对比")
    print("=" * 78)

    # ⚠️ 语料必须**逐位置唯一**。
    #
    # 第一版用同一句话重复 40 次，结果度量报出"重叠 500 字符" ——
    # 那是**假阳性**：相邻块的**内容本身相同**，
    # 于是"最长后缀 = 前缀"必然匹配整块，
    # 与有没有重叠毫无关系。
    #
    # 教训：**用重复内容测重叠必然假阳性**。
    # 因此这里让每个字符都携带自己的下标 ——
    # 位置不同则字符不同，任何真正的重叠都能被精确定位。
    corpus = "".join(
        f"{index:05d}" for index in range(1200)
    )  # 6000 字符，每 5 字符一组、组内唯一

    chunks = ChunkingService.split_text(
        corpus,
        chunk_size=EVAL_CHUNK_SIZE,
        chunk_overlap=EVAL_CHUNK_OVERLAP,
    )

    measured_overlap = measure_overlap(chunks)
    longest = max(len(chunk) for chunk in chunks)

    print(f"      切出 {len(chunks)} 块，最长 {longest} 字符")
    print(f"      量出的相邻重叠：{measured_overlap} 字符")

    result.check(
        "实际块长不超过报告声称的 chunk_size",
        longest <= EVAL_CHUNK_SIZE,
        f"最长 {longest} ≤ {EVAL_CHUNK_SIZE}",
    )

    # ⚠️ **度量的区分力必须先被证明。**
    #
    # 若 `measure_overlap` 永远返回 0，那么下面那条
    # "实际重叠 == 声称值" 就成了恒真断言 —— 它会在
    # overlap 真的错了的时候也通过。那是**假通过**，
    # 比没有断言更糟（它给虚假的安全感）。
    #
    # 因此显式造一个 overlap=120 的切块，验证度量能测出来。
    with_overlap = ChunkingService.split_text(
        corpus,
        chunk_size=EVAL_CHUNK_SIZE,
        chunk_overlap=120,
    )
    probe = measure_overlap(with_overlap)

    result.check(
        "度量本身有效：能测出 120 字符的重叠（非恒真）",
        probe == 120,
        f"量出 {probe}",
    )

    # 这条才是真正抓住 D70 的那一条。
    #
    # 报告曾声称 overlap=100，而实际是 0。
    result.check(
        "实际重叠等于报告声称的 chunk_overlap",
        measured_overlap == EVAL_CHUNK_OVERLAP,
        f"量出 {measured_overlap}，声称 {EVAL_CHUNK_OVERLAP}",
    )

    # 若 overlap 常量被误设成与产品默认值相同（100），
    # 这条会提醒：评估**刻意**用无重叠。
    result.check(
        "评估刻意使用无重叠切块（与产品默认不同）",
        EVAL_CHUNK_OVERLAP == 0,
        f"EVAL_CHUNK_OVERLAP={EVAL_CHUNK_OVERLAP}，"
        f"产品默认={ChunkingService.DEFAULT_CHUNK_OVERLAP}",
    )

    # ================================================
    print()
    print("=" * 78)
    print("② 报告里记录的参数与实际调用参数来自同一常量")
    print("=" * 78)

    # 读 runner 源码，确认没有裸字面量留在调用处。
    #
    # 这是**结构性**断言：只要有人把 `chunk_size=EVAL_CHUNK_SIZE`
    # 改回 `chunk_size=500`，这条就会失败 ——
    # 而那种改动正是 D63/D70 的根因。
    source = Path(
        r"D:\yuxi\MindFlow\backend\app\evaluation\runner.py"
    ).read_text(encoding="utf-8")

    import re

    split_call = re.search(
        r"ChunkingService\.split_text\((.*?)\)", source, re.DOTALL
    )

    result.check(
        "split_text 的调用用常量而非字面量",
        split_call is not None
        and "EVAL_CHUNK_SIZE" in split_call.group(1)
        and "EVAL_CHUNK_OVERLAP" in split_call.group(1),
        "chunk_size=EVAL_CHUNK_SIZE, chunk_overlap=EVAL_CHUNK_OVERLAP"
        if split_call
        else "未找到调用",
    )

    retrieve_call = re.search(
        r"\.retrieve\((.*?)\)", source, re.DOTALL
    )

    result.check(
        "retrieve 的 limit 用常量而非字面量",
        retrieve_call is not None
        and "EVAL_RETRIEVAL_LIMIT" in retrieve_call.group(1),
        f"limit=EVAL_RETRIEVAL_LIMIT（={EVAL_RETRIEVAL_LIMIT}）"
        if retrieve_call
        else "未找到调用",
    )

    # ================================================
    print()
    print("=" * 78)
    print("③ 报告必须说明「这条路径与产品路径不同」")
    print("=" * 78)

    # 为什么不写清楚就是缺陷：`top_k: 5` 与产品的
    # `DEFAULT_LIMIT_PER_TYPE = 5` 数值相同、**语义不同**。
    # 不说明的话，读者会以为本报告测的是产品路径 ——
    # 而 §8.5 反复强调"产品指标 ≠ R@1"。
    from app.services.interview.context_builder import (
        InterviewContextBuilder,
    )

    print(
        f"      评估单次检索：{EVAL_RETRIEVAL_LIMIT} 块/单类型"
    )
    print(
        f"      产品每种类型："
        f"{InterviewContextBuilder.DEFAULT_LIMIT_PER_TYPE} 块"
    )

    result.check(
        "两条路径的数值确实相同（因此必须靠说明区分）",
        EVAL_RETRIEVAL_LIMIT
        == InterviewContextBuilder.DEFAULT_LIMIT_PER_TYPE,
        "两者都是 5 —— 所以报告里必须写清是哪条路径",
    )

    # 用真实的 save_report 结构验证说明字段存在。
    #
    # 不真跑整场评估（那要调模型、耗时且花钱），
    # 而是构造最小结果后调用 save_report，
    # 再读回它写出的文件。
    runner = EvaluationRunner()
    runner.project_id = -1  # 不真的检索

    report_path = runner.save_report(results=[], summary={"total": 0})

    try:
        report = json.loads(
            Path(report_path).read_text(encoding="utf-8")
        )

        path_info = report["retrieval_config"].get("path", {})

        result.check(
            "报告声明了这是评估路径",
            path_info.get("name") == "evaluation",
            f"name={path_info.get('name')!r}",
        )

        result.check(
            "报告明确标注「与产品路径不同」",
            path_info.get("differs_from_product") is True,
        )

        result.check(
            "报告记录了产品路径的对照值",
            path_info.get("product_path", {}).get("limit_per_type")
            == InterviewContextBuilder.DEFAULT_LIMIT_PER_TYPE,
            f"product_path.limit_per_type="
            f"{path_info.get('product_path', {}).get('limit_per_type')}",
        )

        result.check(
            "报告里的 chunk_overlap 与实际一致（D70 的直接回归项）",
            report["chunking"]["chunk_overlap"] == measured_overlap,
            f"报告 {report['chunking']['chunk_overlap']}，"
            f"实测 {measured_overlap}",
        )

        result.check(
            "报告里的 chunk_size 与实际一致",
            report["chunking"]["chunk_size"] == EVAL_CHUNK_SIZE,
        )

        result.check(
            "报告里的 top_k 来自常量",
            report["retrieval_config"]["top_k"] == EVAL_RETRIEVAL_LIMIT,
        )

    finally:
        # 清理这份探针报告 —— 它不是为了留档（内容为空）
        try:
            Path(report_path).unlink()
            print(f"      已删除探针报告 {Path(report_path).name}")
        except OSError:
            pass

    # ================================================
    print()
    print("=" * 78)
    passed = len(result.rows) - len(result.failed)
    print(f"通过 {passed} / {len(result.rows)}")

    if result.failed:
        print("失败项:")
        for label in result.failed:
            print(f"  - {label}")
        return 1

    print("全部通过")
    return 0


if __name__ == "__main__":
    sys.exit(main())
