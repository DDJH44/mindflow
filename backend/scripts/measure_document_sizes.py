"""测量真实文档的体积与块数分布，为上传上限提供依据。

## 为什么必须先测量

"上传大小上限"这种数字最容易拍脑袋 —— 拍了太小会挡住用户的
正常文件，拍了太大等于没做。**而两种情况都要到用户抱怨时才知道。**

因此先从两个来源取真实分布：

1. **库里的真实文档**（用户自己上传的简历等）
2. **评估语料**（`app/evaluation/datasets.py`，刻意设计的四类材料）

然后据此定上限，而不是猜。

## 它输出什么

- 每个文档的**字节数 / 字符数 / 块数**
- 各类型的**最大值与中位数**
- 一个建议的上限区间（取实测最大值的若干倍留余量）

用法：uv run python -m scripts.measure_document_sizes
"""

import asyncio
import statistics
import sys
from pathlib import Path

from sqlalchemy import text

from app.database.session import AsyncSessionLocal


async def main() -> int:
    sys.path.insert(0, r"D:\yuxi\MindFlow\backend")

    from app.evaluation.datasets import DOCUMENTS

    print("=" * 76)
    print("文档体积分布测量")
    print("=" * 76)

    # ---------------- 评估语料 ----------------
    print()
    print("【一】评估语料（刻意设计的四类材料）")
    print()
    print(
        f"  {'材料':<26} {'字符数':>8} {'块数':>6} "
        f"{'含重叠':>8} {'无重叠':>8}"
    )

    corpus = []

    for spec in DOCUMENTS:
        chars = len(spec.content)

        # 两种切块口径都算一遍：
        # 评估用无重叠（chunk_overlap=0），生产用 100 重叠。
        # 块数直接决定嵌入调用次数，因此两个都要看。
        chunk_size = 500
        with_overlap = max(
            1, -(-(chars - 100) // (chunk_size - 100))
        )
        without_overlap = max(1, -(-chars // chunk_size))

        corpus.append(
            {
                "name": spec.name,
                "chars": chars,
                "docs": spec.document_type,
            }
        )

        print(
            f"  {spec.name:<26} {chars:>8} {without_overlap:>6} "
            f"{with_overlap:>8} {without_overlap:>8}"
        )

    corpus_chars = [item["chars"] for item in corpus]

    print()
    print(
        f"  语料字符数：最小 {min(corpus_chars)}、"
        f"中位 {int(statistics.median(corpus_chars))}、"
        f"最大 {max(corpus_chars)}"
    )

    # ---------------- 库里的真实文档 ----------------
    print()
    print("【二】库里真实文档")
    print()

    async with AsyncSessionLocal() as db:
        rows = list(
            (
                await db.execute(
                    text(
                        "SELECT id, original_filename, file_type, "
                        "status, LENGTH(content) AS chars, "
                        "(SELECT COUNT(*) FROM document_chunks c "
                        " WHERE c.document_id = d.id) AS chunks "
                        "FROM documents d ORDER BY LENGTH(content) DESC"
                    )
                )
            ).all()
        )

    if not rows:
        print("  库里没有文档")
    else:
        print(
            f"  {'id':>4} {'文件名':<30} {'类型':<6} "
            f"{'字符数':>9} {'块数':>6}"
        )

        for row in rows:
            name = str(row[1] or "")[:28]
            # `content` 可能为 NULL（记录已建但解析/切块未完成的文档），
            # 直接格式化会抛 `TypeError: unsupported format string
            # passed to NoneType` —— 那是测量脚本自己的问题，
            # 但它会让人以为是数据坏了。
            chars = row[4] if row[4] is not None else 0

            print(
                f"  {row[0]:>4} {name:<30} "
                f"{str(row[2] or ''):<6} "
                f"{chars:>9} {row[5]:>6}"
            )

        real_chars = [row[4] for row in rows if row[4]]
        real_chunks = [row[5] for row in rows]

        if real_chars:
            print()
            print(
                f"  真实文档字符数：最小 {min(real_chars)}、"
                f"中位 {int(statistics.median(real_chars))}、"
                f"最大 {max(real_chars)}"
            )
            print(
                f"  真实文档块数：  最小 {min(real_chunks)}、"
                f"中位 {int(statistics.median(real_chunks))}、"
                f"最大 {max(real_chunks)}"
            )

    # ---------------- 磁盘上的上传文件 ----------------
    print()
    print("【三】磁盘上的上传文件（真实字节数）")
    print()

    # 相对包根推导，不硬编码盘符 ——
    # 硬编码在容器里会变成"目录不存在"，而那会被误读成
    # "磁盘上没有上传文件"（本工具的结论会因此失真）。
    upload_dir = Path(__file__).resolve().parents[2] / "uploads"

    if not upload_dir.exists():
        print("  上传目录不存在")
    else:
        files = [
            item
            for item in upload_dir.rglob("*")
            if item.is_file()
        ]

        if not files:
            print("  目录为空")
        else:
            sizes = sorted(item.stat().st_size for item in files)

            print(f"  文件数 {len(files)}")
            print(
                f"  字节数：最小 {sizes[0]}、"
                f"中位 {int(statistics.median(sizes))}、"
                f"最大 {sizes[-1]}"
            )

            for item in sorted(
                files, key=lambda p: p.stat().st_size, reverse=True
            )[:5]:
                print(
                    f"    {item.stat().st_size:>10} 字节  "
                    f"{item.name}"
                )

    # ---------------- 建议 ----------------
    print()
    print("=" * 76)
    print("依据")
    print("=" * 76)

    all_chars = corpus_chars
    if rows:
        all_chars = all_chars + [row[4] for row in rows if row[4]]

    observed_max = max(all_chars)

    print(f"  实测最大字符数：{observed_max}")
    print()
    print("  取上限时的两个约束：")
    print()
    print("  ① **必须容得下实测最大值，并留足余量** ——")
    print("     真实用户的材料可能比现有样本大得多")
    print("     （例如一份几十页的项目文档、一个上万行的代码文件）")
    print("  ② **必须挡得住会烧钱的输入** ——")
    print("     块数决定嵌入调用次数（按量计费）")
    print()
    print("  块数与字符数的关系（chunk_size=500、overlap=100）：")
    for chars in (5_000, 50_000, 200_000, 500_000, 1_000_000):
        chunks = max(1, -(-(chars - 100) // 400))
        print(
            f"    {chars:>9} 字符 → 约 {chunks:>5} 块 "
            f"→ {chunks:>5} 次嵌入调用"
        )

    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
