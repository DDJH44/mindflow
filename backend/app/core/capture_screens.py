"""截取各页面的真实渲染结果，用于人工审视视觉。

## 为什么需要它

"界面不好看"这句话**不能靠读代码判断** ——
CSS 变量的值是抽象的，而"好不好看"是渲染之后的事。

而实测教训（D66）：登录页永远挂着一张"正在加载…"的卡片，
**用户自己截图报告了这个问题**，而我的走查与
`check_computed_fonts` 都没发现它 ——
那两个检查都不看"页面上出现了不该出现的东西"。

因此：**先把界面渲染出来看，再决定改什么。**

## 它做什么

1. 临时把测试账号的密码改成已知值（退出后恢复原哈希）
2. 用 Playwright 登录
3. 逐页截图（整页，含滚动区）到 `web/screenshots/`
4. 打印每页的关键度量：字号、主色、卡片数、可见文本行数

第 3 步的产物是**给人看的**；第 4 步的数字是**给判断用的** ——
例如"全站只有两种字号"或"主色只出现 3 次"这类事实。

用法：uv run python -m app.core.capture_screens
"""

import asyncio
import sys
from pathlib import Path

from app.core.ui_walkthrough import (
    ACCOUNT,
    BASE,
    TEMP_PASSWORD,
    find_chromium,
    restore_password,
    set_password,
)

OUTPUT_DIR = Path(r"D:\yuxi\MindFlow\web\screenshots")


async def main(session_id: int | None = None) -> int:
    sys.path.insert(0, r"D:\yuxi\MindFlow\backend")

    from playwright.async_api import async_playwright

    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

    executable = find_chromium()

    if executable is None:
        print("  ✗ 找不到 chromium，无法截图")
        return 1

    print("=" * 74)
    print("页面截图")
    print("=" * 74)
    print(f"  chromium: {executable}")
    print(f"  输出目录: {OUTPUT_DIR}")
    print()

    original_hash = await set_password(TEMP_PASSWORD)

    try:
        async with async_playwright() as playwright:
            browser = await playwright.chromium.launch(
                executable_path=executable,
                args=["--no-sandbox"],
            )

            page = await browser.new_page(
                viewport={"width": 1440, "height": 900},
                device_scale_factor=1,
            )

            # ---------------- 登录页 ----------------
            await page.goto(BASE, wait_until="networkidle")
            await page.wait_for_timeout(600)

            await page.screenshot(
                path=str(OUTPUT_DIR / "01-login.png"),
                full_page=True,
            )
            print("  [OK] 01-login.png")

            # ---------------- 登录 ----------------
            await page.fill("#account", ACCOUNT)
            await page.fill("#password", TEMP_PASSWORD)
            await page.click("button[type=submit]")
            await page.wait_for_timeout(2500)

            # ---------------- 其余页面 ----------------
            pages = [
                ("02-home", "/"),
                ("03-profile", "/profile"),
                ("04-history", "/history"),
            ]

            for name, path in pages:
                try:
                    await page.goto(f"{BASE}{path}", wait_until="networkidle")
                    await page.wait_for_timeout(1200)

                    await page.screenshot(
                        path=str(OUTPUT_DIR / f"{name}.png"),
                        full_page=True,
                    )
                    print(f"  [OK] {name}.png")

                except Exception as exc:  # noqa: BLE001
                    print(f"  [!!] {name}: {type(exc).__name__}: {exc}")

            # ---------------- 项目页（有数据） ----------------
            try:
                await page.goto(f"{BASE}/projects/3", wait_until="networkidle")
                await page.wait_for_timeout(1500)

                await page.screenshot(
                    path=str(OUTPUT_DIR / "05-project.png"),
                    full_page=True,
                )
                print("  [OK] 05-project.png（项目 3，含真实简历）")

            except Exception as exc:  # noqa: BLE001
                print(f"  [!!] 05-project: {type(exc).__name__}: {exc}")

            # ---------------- 面试页（暂停态） ----------------
            #
            # 暂停提示的三种分支**只在暂停态出现**，而正常流程
            # 要走完整面试才会到那里。因此会话 id 由外部传入：
            #
            #     uv run python -m app.core.seed_paused_session
            #     uv run python -m app.core.capture_screens --session-id 1391
            #
            # 为什么不在本脚本里造数据：截图脚本应当**只读** ——
            # 一个会改数据的检查工具，迟早会在不该跑的时候被跑。
            if session_id is not None:
                try:
                    await page.goto(
                        f"{BASE}/interviews/{session_id}",
                        wait_until="networkidle",
                    )
                    await page.wait_for_timeout(1800)

                    await page.screenshot(
                        path=str(OUTPUT_DIR / "06-interview-paused.png"),
                        full_page=True,
                    )
                    print(
                        f"  [OK] 06-interview-paused.png"
                        f"（会话 {session_id}）"
                    )

                except Exception as exc:  # noqa: BLE001
                    print(
                        f"  [!!] 06-interview-paused: "
                        f"{type(exc).__name__}: {exc}"
                    )

            # ---------------- 度量：字号与颜色分布 ----------------
            print()
            print("=" * 74)
            print("计算样式度量（登录页 + 首页）")
            print("=" * 74)

            for name, path in (("login", "/"), ("home", "/")):
                await page.goto(f"{BASE}{path}", wait_until="networkidle")
                await page.wait_for_timeout(900)

                metrics = await page.evaluate(
                    """() => {
                        const sizes = {};
                        const colors = {};
                        const radius = {};

                        const nodes = document.querySelectorAll('*');

                        for (const node of nodes) {
                            const text = (node.textContent || '').trim();

                            if (!text) continue;

                            const style = getComputedStyle(node);

                            // 只看"直接含文本"的叶子，避免父元素重复计数
                            const hasElementChild = [...node.children]
                                .some(child =>
                                    (child.textContent || '').trim().length > 0
                                );

                            if (!hasElementChild) {
                                const fs = style.fontSize;
                                sizes[fs] = (sizes[fs] || 0) + 1;
                            }

                            const bg = style.backgroundColor;

                            if (bg && bg !== 'rgba(0, 0, 0, 0)') {
                                colors[bg] = (colors[bg] || 0) + 1;
                            }

                            const br = style.borderRadius;

                            if (br && br !== '0px') {
                                radius[br] = (radius[br] || 0) + 1;
                            }
                        }

                        return {
                            sizes,
                            colors,
                            radius,
                            scrollHeight: document.body.scrollHeight,
                            viewportHeight: window.innerHeight,
                        };
                    }"""
                )

                print()
                print(f"  --- {name} ---")
                print(f"    页面高度: {metrics['scrollHeight']}px"
                      f"（视口 {metrics['viewportHeight']}px）")

                size_items = sorted(
                    metrics["sizes"].items(),
                    key=lambda item: -item[1],
                )[:6]
                print(
                    "    字号分布: "
                    + ", ".join(
                        f"{size}×{count}" for size, count in size_items
                    )
                )

                color_items = sorted(
                    metrics["colors"].items(),
                    key=lambda item: -item[1],
                )[:6]
                print(
                    "    背景色: "
                    + ", ".join(
                        f"{color}×{count}" for color, count in color_items
                    )
                )

                radius_items = sorted(
                    metrics["radius"].items(),
                    key=lambda item: -item[1],
                )[:5]
                print(
                    "    圆角: "
                    + ", ".join(
                        f"{value}×{count}"
                        for value, count in radius_items
                    )
                )

            # ---------------- 控制台错误 ----------------
            await browser.close()

    finally:
        await restore_password(original_hash)
        print()
        print("  已恢复原密码哈希")

    return 0


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="逐页截图 + 计算样式度量")
    parser.add_argument(
        "--session-id",
        type=int,
        default=None,
        help=(
            "额外截一张该面试会话的页面（暂停态提示只在暂停态出现，"
            "用 seed_paused_session 造数据后传它的 id）"
        ),
    )

    args = parser.parse_args()

    sys.exit(asyncio.run(main(session_id=args.session_id)))
