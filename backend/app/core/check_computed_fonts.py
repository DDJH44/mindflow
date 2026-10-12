"""核对排版令牌在浏览器里的**计算值**（常驻检查）。

## 为什么不满足于"源码看起来对"

CSS 变量在浏览器里的实际行为有三处容易出错，
而且**都不会报错**：

1. **令牌未定义** —— `var(--fs-xxl)` 拼错时浏览器静默回退到
   继承值。源码里看着没问题，界面上字号不对。
2. **更具体的选择器覆盖** —— `.page-head h1 { font-size: ... }`
   会盖掉 `h1`，而 grep `h1` 只看到一处。
3. **相对单位残留** —— 若将来有人写 `1.1em`，
   它会随父级变化，而"收敛到令牌"的假设就破了。

因此必须有一步**在真实浏览器里读 `getComputedStyle`**。

## 它检查什么

1. 七个字号令牌都能解析成预期值（未定义会返回空串）
2. 关键元素（h1 / h2 / body）的计算字号等于对应令牌
3. **所有渲染元素**的字号都落在既定阶梯内 ——
   排除 `<html>` / `<head>` / `<title>` / `<style>` / `<script>`：
   它们的 16px 是浏览器默认值，不是我们写的样式
   （第一版没排除，于是每次都误报一个 16px）

用法：uv run python -m app.core.check_computed_fonts
"""

import asyncio
import glob
import sys

from playwright.async_api import async_playwright
from sqlalchemy import text

from app.database.session import AsyncSessionLocal

BASE = "http://127.0.0.1:5173"
ACCOUNT = "mindflow"

# 预期值必须与 `app.css` 的 `:root` 一致。
EXPECTED = {
    "--fs-xs": "12px",
    "--fs-sm": "13px",
    "--fs-base": "14px",
    "--fs-md": "15px",
    "--fs-lg": "18px",
    "--fs-xl": "22px",
    "--fs-display": "26px",
}

# 元素 → 它应当使用的令牌
ELEMENT_CHECKS = (
    ("h1", "--fs-xl"),
    ("h2", "--fs-lg"),
    ("body", "--fs-base"),
)

# 要访问的页面（覆盖不同布局）
PAGES = (
    ("/", "首页"),
    ("/history", "历史页"),
    ("/profile", "能力画像页"),
)

# 非渲染元素：它们的字号来自浏览器默认样式，
# 不属于我们控制的排版，因此不参与"阶梯外字号"判定。
NON_RENDERED = ("HTML", "HEAD", "TITLE", "STYLE", "SCRIPT", "META", "LINK")


def find_chromium() -> str | None:
    for pattern in (
        r"C:\Users\*\AppData\Local\ms-playwright\chromium-*\chrome-win64\chrome.exe",
        r"C:\Users\*\AppData\Local\ms-playwright\chromium-*\chrome-win\chrome.exe",
    ):
        found = glob.glob(pattern)
        if found:
            return found[0]
    return None


async def main() -> int:
    from app.core.security import create_access_token

    executable = find_chromium()

    if not executable:
        print("  找不到 chromium，跳过（需要 uv run playwright install）")
        return 2  # 依赖不可用 —— 与项目其它脚本的约定一致

    async with AsyncSessionLocal() as db:
        user_id = (
            await db.execute(
                text("SELECT id FROM users WHERE username = :u"),
                {"u": ACCOUNT},
            )
        ).scalar_one()

    token = create_access_token({"sub": str(user_id)})

    failures: list[str] = []
    checked = 0

    async with async_playwright() as playwright:
        browser = await playwright.chromium.launch(
            executable_path=executable, headless=True
        )
        context = await browser.new_context(
            viewport={"width": 1280, "height": 900}
        )
        await context.add_init_script(
            f"window.localStorage.setItem('mindflow.token', {token!r});"
        )
        page = await context.new_page()

        try:
            for path, label in PAGES:
                await page.goto(f"{BASE}{path}", wait_until="networkidle")
                await page.wait_for_timeout(2200)

                print()
                print("=" * 74)
                print(f"{label}  {path}")
                print("=" * 74)

                # ---- 令牌解析 ----
                names = ", ".join(f"'{n}'" for n in EXPECTED)

                resolved = await page.evaluate(
                    "() => {"
                    "  const s = getComputedStyle("
                    "    document.documentElement);"
                    f"  const names = [{names}];"
                    "  const out = {};"
                    "  for (const n of names) {"
                    "    out[n] = s.getPropertyValue(n).trim();"
                    "  }"
                    "  return out;"
                    "}"
                )

                for name, expected in EXPECTED.items():
                    actual = resolved.get(name) or ""
                    ok = actual == expected
                    checked += 1

                    if not ok:
                        failures.append(
                            f"{name} 期望 {expected}，实得 "
                            f"{actual or '(未定义)'}"
                        )

                print(
                    "  令牌: "
                    + "  ".join(
                        f"{n.replace('--fs-', '')}="
                        f"{resolved.get(n) or '空'}"
                        for n in EXPECTED
                    )
                )

                # ---- 关键元素 ----
                for selector, token in ELEMENT_CHECKS:
                    element = await page.query_selector(selector)

                    if element is None:
                        continue

                    actual = await element.evaluate(
                        "el => getComputedStyle(el).fontSize"
                    )
                    expected = EXPECTED[token]
                    checked += 1

                    ok = actual == expected

                    if not ok:
                        failures.append(
                            f"{label} {selector} 应为 {token}"
                            f"（{expected}），实得 {actual}"
                        )

                    print(
                        f"  {selector:<6} = {actual:<6} "
                        f"({'✓' if ok else '✗ 期望 ' + expected})"
                    )

                # ---- 渲染元素的字号集合 ----
                sizes = await page.evaluate(
                    """(excluded) => {
                        const seen = new Set();
                        for (const el of document.querySelectorAll('*')) {
                            if (excluded.includes(el.tagName)) continue;
                            const text = (el.textContent || '').trim();
                            if (!text) continue;
                            seen.add(
                                getComputedStyle(el).fontSize
                            );
                        }
                        return [...seen].sort(
                            (a, b) => parseFloat(a) - parseFloat(b)
                        );
                    }""",
                    list(NON_RENDERED),
                )

                allowed = set(EXPECTED.values())
                stray = [s for s in sizes if s not in allowed]

                checked += 1

                print(f"  渲染字号: {sizes}")

                if stray:
                    failures.append(f"{label} 出现阶梯外字号 {stray}")
                    print(f"    ✗ 阶梯外: {stray}")
                else:
                    print("    ✓ 全部在阶梯内")

        finally:
            await browser.close()

    print()
    print("=" * 74)

    if failures:
        print(f"  断言 {checked} 项，失败 {len(failures)} 项：")
        for item in failures:
            print(f"    - {item}")
        return 1

    print(f"  ✓ 断言 {checked} 项全部通过 —— 排版令牌的实际计算值符合预期")
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
