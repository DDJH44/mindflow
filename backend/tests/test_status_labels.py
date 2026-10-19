"""验证会话状态的英文枚举没有漏到界面上（§10.6 / 新缺陷类）。

## 它防的是什么

实测：面试页的标签写的是

    <span class="tag">{{ session.status }}</span>

于是界面上出现英文 **`paused`**，而历史页与项目页早就各自
做过中文映射。**同一个映射被抄在两处时，漏掉第三处不会报错** ——
它只会让界面冒出一个英文单词。

而这个缺陷只能靠**看界面**发现：类型检查通不过不了（
`session.status` 是合法的 `string`），逻辑测试也发现不了
（接口返回的确实是 `paused`，数据没错）。

## 它验证两件事

1. **纯逻辑**：后端 `SessionStatus` 的**每一个**取值
   都有对应的中文标签 —— 否则新增状态时会漏
2. **浏览器**：面试页（含暂停态）的可见文本里
   **不出现**裸的英文状态值

第 2 条需要一条 paused 会话。没有时**明确跳过**（退出码 2），
而不是假装通过 —— 与门禁其它套件的约定一致。

用法：uv run python -m tests.test_status_labels
"""

import asyncio
import re
import sys

from sqlalchemy import text

from app.database.session import AsyncSessionLocal
from tests.ui_walkthrough import (
    ACCOUNT,
    BASE,
    TEMP_PASSWORD,
    find_chromium,
    restore_password,
    set_password,
)


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


# 前端标签模块的路径。这里直接用正则读它而不是跑 JS：
# 目标是"键集合是否覆盖后端取值"，那件事用文本就能判定，
# 起一个 node 进程反而更脆。
LABELS_TS = r"D:\yuxi\MindFlow\web\src\api\labels.ts"


def read_label_keys() -> set[str]:
    """从 `labels.ts` 里读出 `STATUS_LABEL` 的键。"""

    content = open(LABELS_TS, encoding="utf-8").read()

    block = re.search(
        r"STATUS_LABEL[^=]*=\s*\{(.*?)\}", content, re.DOTALL
    )

    if block is None:
        return set()

    return set(re.findall(r"^\s*(\w+):", block.group(1), re.MULTILINE))


async def main() -> int:
    sys.path.insert(0, r"D:\yuxi\MindFlow\backend")

    from app.services.interview.interview_state_machine import (
        SessionStatus,
    )

    result = Result()

    print("=" * 78)
    print("状态标签覆盖性验证")
    print("=" * 78)

    # ================================================
    print()
    print("=" * 78)
    print("① 后端每个状态都有中文标签")
    print("=" * 78)

    backend_statuses = {item.value for item in SessionStatus}
    label_keys = read_label_keys()

    print(f"      后端状态: {sorted(backend_statuses)}")
    print(f"      标签键:   {sorted(label_keys)}")

    missing = backend_statuses - label_keys

    result.check(
        "标签覆盖了后端的全部状态",
        not missing,
        f"缺失 {sorted(missing)}" if missing else f"{len(label_keys)} 个键",
    )

    # 反向检查：标签里不该有后端根本不存在的状态。
    #
    # ⚠️ 但要排除**历史遗留值** —— 它们不在枚举里，
    # 却仍然存在于数据库中（迁移前的字面值）。
    #
    # 实测这条检查第一版报了 `多余 ['created']`，而那不是错误：
    # 库里确实有 `status='created'` 的会话（D58），
    # 历史页的注释也专门写了这一点 —— 少了它这类会话
    # 会显示成"—"、看起来打不开。
    #
    # 因此这份名单必须**显式列出**：它是有意的兼容，
    # 不是拼写错误。新增"多余键"时，要么是真拼错，
    # 要么该登记到这里 —— 两种都需要人看一眼。
    LEGACY_STATUSES = {
        "created",  # D58：迁移前的字面值，库里仍有数据
    }

    extra = label_keys - backend_statuses - LEGACY_STATUSES

    result.check(
        "标签里没有拼错的状态名（历史遗留值已显式登记）",
        not extra,
        f"多余 {sorted(extra)}"
        if extra
        else f"仅历史遗留值 {sorted(label_keys & LEGACY_STATUSES)}",
    )

    # ================================================
    print()
    print("=" * 78)
    print("② 浏览器：面试页不出现裸的英文状态值")
    print("=" * 78)

    # 找一条 paused 会话（暂停态最能暴露这个问题：
    # 它的标签正是实测里漏成 `paused` 的那一处）
    async with AsyncSessionLocal() as db:
        row = (
            await db.execute(
                text(
                    """
                    SELECT s.id
                    FROM interview_sessions s
                    JOIN users u ON u.id = s.user_id
                    WHERE u.username = :u AND s.status = 'paused'
                    ORDER BY s.id DESC
                    LIMIT 1
                    """
                ),
                {"u": ACCOUNT},
            )
        ).first()

    if row is None:
        print()
        print("  ⚠️ 没有 paused 会话，本次**未执行**浏览器检查")
        print("     先跑 uv run python -m tests.seed_paused_session")
        print("     再带 --session-id 重跑本套件")
        print()
        print(f"  逻辑部分：通过 "
              f"{len(result.rows) - len(result.failed)} / "
              f"{len(result.rows)}")
        return 2

    session_id = row[0]

    executable = find_chromium()

    if executable is None:
        print("  ⚠️ 找不到 chromium，本次**未执行**浏览器检查")
        return 2

    from playwright.async_api import async_playwright

    original_hash = await set_password(TEMP_PASSWORD)

    try:
        async with async_playwright() as playwright:
            browser = await playwright.chromium.launch(
                executable_path=executable,
                args=["--no-sandbox"],
            )

            page = await browser.new_page(
                viewport={"width": 1440, "height": 900}
            )

            await page.goto(BASE, wait_until="networkidle")
            await page.wait_for_timeout(500)

            await page.fill("#account", ACCOUNT)
            await page.fill("#password", TEMP_PASSWORD)
            await page.click("button[type=submit]")
            await page.wait_for_timeout(2000)

            await page.goto(
                f"{BASE}/interviews/{session_id}",
                wait_until="networkidle",
            )
            await page.wait_for_timeout(1500)

            body_text = await page.inner_text("body")

            await browser.close()

    finally:
        await restore_password(original_hash)

    # 判据：可见文本里不该出现"独立的英文状态词"。
    #
    # 用词边界匹配，避免把 "paused_at" 之类的属性名误判
    # （它们不会出现在可见文本里，但万一出现也不该算命中）。
    leaked = [
        status
        for status in sorted(backend_statuses)
        if re.search(rf"(?<![a-z_]){re.escape(status)}(?![a-z_])", body_text)
    ]

    if leaked:
        print(f"      页面文本片段：{body_text[:200]!r}")

    result.check(
        "页面上没有裸的英文状态值",
        not leaked,
        f"泄漏 {leaked}" if leaked else "全部为中文标签",
    )

    # 另外确认中文标签真的出现了 —— 只断言"没有英文"
    # 会被一个**空页面**满足（D66 的教训）。
    result.check(
        "页面确实渲染了中文状态标签",
        "已暂停" in body_text,
        "含「已暂停」" if "已暂停" in body_text else "未找到「已暂停」",
    )

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
    sys.exit(asyncio.run(main()))
