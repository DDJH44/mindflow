"""验证门禁自己的失败报告：能不能说清"哪条断言断了"。

## 为什么值得单独测

门禁的价值取决于**失败信息能不能直接定位问题**。它原先的写法是：

    reason = extract_counts(output) or failure or f"退出码 {code}"

`extract_counts` 返回 "3/4" 这类非空字符串，于是**总是先命中**，
后面辛苦找到的失败断言标签永远显示不出来。门禁只说"3/4"，
不说哪条断了 —— 实测排查时两次都得重跑套件才知道。

这类缺陷不会让门禁误报，只会让它**变得没用**，因此很容易长期存在。

用法：uv run python -m tests.test_gate_reporting
"""

import sys


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


def main() -> int:
    sys.path.insert(0, r"D:\yuxi\MindFlow\backend")

    from tests.test_all import build_failure_reason

    result = Result()

    print("=" * 78)
    print("门禁失败报告验证")
    print("=" * 78)

    print()
    print("=" * 78)
    print("① 有失败标签时，标签必须出现")
    print("=" * 78)

    sample = """[OK  ] 某条通过
[FAIL] 沟通维度不应被内容不足拉低  实得 D
通过 3 / 4"""

    reason = build_failure_reason(sample, 1)

    result.check(
        "标签出现（而不是只有 3/4）",
        "沟通维度不应被内容不足拉低" in reason,
        reason,
    )
    result.check(
        "计数也保留（说明断了几条）",
        "3/4" in reason,
        reason,
    )

    # ================================================
    print()
    print("=" * 78)
    print("② 没有标签时退化")
    print("=" * 78)

    result.check(
        "只有计数 → 显示计数",
        build_failure_reason("通过 3 / 4", 1) == "3/4",
        build_failure_reason("通过 3 / 4", 1),
    )

    crash = "Traceback (most recent call last):\nRuntimeError: boom"
    result.check(
        "崩溃 → 显示错误行（不显示退出码 1 这种无用信息）",
        "RuntimeError" in build_failure_reason(crash, 1),
        build_failure_reason(crash, 1),
    )

    result.check(
        "什么都没有 → 退出码",
        build_failure_reason("", 3) == "退出码 3",
        build_failure_reason("", 3),
    )

    # ================================================
    print()
    print("=" * 78)
    print("③ 防回归：标签不能被计数顶掉")
    print("=" * 78)

    # 这一条钉的正是修过的那个 bug：只要同时存在计数与标签，
    # 标签就必须出现。若有人把优先级改回去，这里立刻失败。
    text = """[FAIL] 唯一一条失败的断言
通过 9 / 10"""

    result.check(
        "同时有时，标签必须出现（防优先级被改回）",
        "唯一一条失败的断言" in build_failure_reason(text, 1),
        build_failure_reason(text, 1),
    )

    # ================================================
    print()
    print("=" * 78)
    print("④ 失败标签里换行/多余空白要压平")
    print("=" * 78)

    multiline = "[FAIL] 第一行\n    第二行说明\n通过 1 / 2"
    flat = build_failure_reason(multiline, 1)

    result.check(
        "结果里不含换行（否则汇总行会被撑开）",
        "\n" not in flat,
        repr(flat[:60]),
    )

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
