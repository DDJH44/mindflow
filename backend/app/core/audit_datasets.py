"""统计两个评估数据集的覆盖情况。"""

import sys
from collections import Counter


def main() -> int:
    sys.path.insert(0, r"D:\yuxi\MindFlow\backend")

    print("=" * 74)
    print("RAG 检索数据集")
    print("=" * 74)

    from app.evaluation.datasets import (
        DOCUMENTS,
        EVALUATION_CASES,
    )

    print(f"  语料 {len(DOCUMENTS)} 份：")

    for document in DOCUMENTS:
        print(
            f"    {document.name[:30]:<32} "
            f"type={document.document_type:<9} "
            f"chars={len(document.content)}"
        )

    print(f"  用例 {len(EVALUATION_CASES)} 个：")

    by_type = Counter(
        item.document_type for item in EVALUATION_CASES
    )
    by_diff = Counter(item.difficulty for item in EVALUATION_CASES)

    for name, count in sorted(by_type.items(), key=lambda kv: str(kv[0])):
        print(f"    type={str(name):<10} {count}")

    print(f"  按难度：{dict(by_diff)}")

    print()
    print("=" * 74)
    print("面试评估数据集")
    print("=" * 74)

    from app.evaluation.interview.datasets import (
        INTERVIEW_EVALUATION_CASES,
    )

    total_turns = 0

    print(f"  用例 {len(INTERVIEW_EVALUATION_CASES)} 个：")

    for case in INTERVIEW_EVALUATION_CASES:
        turns = len(case["turns"])
        total_turns += turns

        # 统计该用例覆盖了哪些断言维度
        features = set()

        for turn in case["turns"]:
            if turn.get("expected_missing_points"):
                features.add("缺口识别")
            if turn.get("expected_follow_up_keywords"):
                features.add("追问关键词")
            if turn.get("min_analyzer_score") is not None:
                features.add("评分门槛")
            if turn.get("max_analyzer_score") is not None:
                features.add("评分上限")

        print(
            f"    {case['id']:<30} turns={turns}  "
            f"[{' '.join(sorted(features)) or '无断言'}]"
        )

    print(f"  合计 {total_turns} 轮")

    print()
    print("  说明：本数据集不按 document_type 分类 ——")
    print("  它评估的是**评估器本身**（题面理解、缺口识别、追问质量），")
    print("  而不是检索。因此'扩到 jd/project/code'这条待办")
    print("  对它并不适用（那条是针对 RAG 数据集的）。")

    return 0


if __name__ == "__main__":
    sys.exit(main())
