"""截取"注册"状态的登录页（用于确认邀请码输入框是否出现）。"""

import asyncio
import sys
from pathlib import Path

from tests.ui_walkthrough import BASE, find_chromium

OUTPUT = Path(r"D:\yuxi\MindFlow\web\screenshots\08-register.png")


async def main() -> int:
    sys.path.insert(0, r"D:\yuxi\MindFlow\backend")

    from playwright.async_api import async_playwright

    executable = find_chromium()

    if executable is None:
        print("  ✗ 找不到 chromium")
        return 1

    async with async_playwright() as playwright:
        browser = await playwright.chromium.launch(
            executable_path=executable,
            args=["--no-sandbox"],
        )

        page = await browser.new_page(
            viewport={"width": 1440, "height": 1000}
        )

        await page.goto(BASE, wait_until="networkidle")
        await page.wait_for_timeout(600)

        # 点"去注册"
        await page.click("text=去注册")
        await page.wait_for_timeout(800)

        await page.screenshot(path=str(OUTPUT), full_page=True)

        # 断言邀请码输入框存在
        invite = await page.query_selector("#invite-code")
        body = await page.inner_text("body")

        print(f"  [OK] {OUTPUT.name}")
        print(f"  邀请码输入框存在: {invite is not None}")
        print(f"  页面含「邀请码」字样: {'邀请码' in body}")

        await browser.close()

    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
