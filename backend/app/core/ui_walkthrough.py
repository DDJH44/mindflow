"""用无头浏览器真实加载前端并截图，验证界面。

为什么需要它：类型检查与接口测试都**看不到“页面长什么样”** ——
Vue 挂载失败、布局错位、文案写错、按钮点不动，这些只有真的渲染
一次才能发现。HTTP 200 只说明服务器在响应，不说明页面能用。

做法：
1. 临时把 `mindflow` 的密码改为已知值，**结束时恢复原哈希**
2. 用 Playwright（全局 Python 有）驱动 chromium 走完整流程并截图
3. 收集控制台错误与页面异常

用法：
    D:\\Develop\\python\\python.exe <本脚本>
"""

import asyncio
import json
import sys
from pathlib import Path

from playwright.async_api import async_playwright

sys.path.insert(0, r"D:\yuxi\MindFlow\backend")

from sqlalchemy import text as sql_text  # noqa: E402

from app.database.session import AsyncSessionLocal  # noqa: E402

BASE = "http://127.0.0.1:5173"
SHOTS = Path(r"D:\yuxi\MindFlow\docs\ui-shots")
TEMP_PASSWORD = "temp_ui_walk_123"
ACCOUNT = "mindflow"

console_errors: list[str] = []
page_errors: list[str] = []
notes: list[str] = []


def note(text: str) -> None:
    notes.append(text)
    print(f"  {text}")


async def set_password(password: str) -> str:
    """改密码，返回原哈希以便恢复。"""

    from sqlalchemy import text

    from app.core.security import hash_password
    from app.database.session import AsyncSessionLocal

    async with AsyncSessionLocal() as db:
        original = (
            await db.execute(
                text(
                    "SELECT password_hash FROM users "
                    "WHERE username = :u"
                ),
                {"u": ACCOUNT},
            )
        ).scalar_one()

        await db.execute(
            text(
                "UPDATE users SET password_hash = :h "
                "WHERE username = :u"
            ),
            {"h": hash_password(password), "u": ACCOUNT},
        )
        await db.commit()

    return original


async def restore_password(original: str) -> None:
    from sqlalchemy import text

    from app.database.session import AsyncSessionLocal

    async with AsyncSessionLocal() as db:
        await db.execute(
            text(
                "UPDATE users SET password_hash = :h "
                "WHERE username = :u"
            ),
            {"h": original, "u": ACCOUNT},
        )
        await db.commit()


async def shot(page, name: str) -> None:
    SHOTS.mkdir(parents=True, exist_ok=True)
    path = SHOTS / f"{name}.png"
    await page.screenshot(path=str(path), full_page=True)
    size = path.stat().st_size
    note(f"截图 {name}.png（{size // 1024} KB）")


def find_chromium() -> str | None:
    """找本地已有的 chromium 可执行文件。

    不跑 `playwright install`：本机 ms-playwright 缓存里已有多个版本，
    但都不是当前 playwright 包期望的那个编号。指定 `executable_path`
    即可复用，省掉一次几百 MB 的下载。
    """

    root = Path.home() / "AppData/Local/ms-playwright"
    if not root.exists():
        # 也认 Chrome / Edge
        for candidate in (
            r"C:\Program Files\Google\Chrome\Application\chrome.exe",
            r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe",
        ):
            if Path(candidate).exists():
                return candidate
        return None

    # 优先完整 chromium（headless shell 不支持部分截图选项）
    for folder in sorted(root.glob("chromium-*"), reverse=True):
        exe = folder / "chrome-win64/chrome.exe"
        if exe.exists():
            return str(exe)

    for folder in sorted(root.glob("chromium_headless_shell-*"), reverse=True):
        exe = folder / "chrome-headless-shell-win64/chrome-headless-shell.exe"
        if exe.exists():
            return str(exe)

    return None


async def main() -> int:
    original_hash = await set_password(TEMP_PASSWORD)

    try:
        exe = find_chromium()
        print(f"  使用浏览器: {exe}")

        async with async_playwright() as playwright:
            browser = await playwright.chromium.launch(
                headless=True,
                executable_path=exe,
            )
            page = await browser.new_page(
                viewport={"width": 1280, "height": 900}
            )

            page.on(
                "console",
                lambda msg: (
                    console_errors.append(msg.text)
                    if msg.type == "error"
                    else None
                ),
            )
            page.on("pageerror", lambda err: page_errors.append(str(err)))

            # ---------------- 登录页 ----------------
            print()
            print("=" * 74)
            print("1. 登录页")
            print("=" * 74)

            await page.goto(BASE, wait_until="networkidle")
            await page.wait_for_timeout(800)

            heading = await page.text_content("h1")
            note(f"页面标题: {await page.title()}")
            note(f"主标题: {heading}")

            app_html = await page.inner_html("#app")
            note(f"#app 已渲染内容: {len(app_html) > 50}")

            await shot(page, "01-login")

            # ---------------- 登录 ----------------
            print()
            print("=" * 74)
            print("2. 登录")
            print("=" * 74)

            await page.fill("#account", ACCOUNT)
            await page.fill("#password", TEMP_PASSWORD)
            await page.click("button[type=submit]")
            await page.wait_for_url(f"{BASE}/", timeout=30_000)
            await page.wait_for_timeout(2500)

            note(f"登录后 URL: {page.url}")
            body = await page.text_content("body")
            note(f"顶栏显示用户名: {'mindflow' in body}")

            await shot(page, "02-home")

            # ---------------- 首页内容 ----------------
            print()
            print("=" * 74)
            print("3. 首页内容")
            print("=" * 74)

            h1 = await page.text_content("h1")
            note(f"主标题: {h1}")

            # 额度提示
            alert = await page.query_selector(".alert")
            if alert:
                note(f"额度提示: {(await alert.text_content()).strip()[:70]}")

            # 项目列表
            items = await page.query_selector_all(".list-item")
            note(f"项目数: {len(items)}")
            for item in items[:3]:
                name = (await item.text_content()).strip().split("\n")[0]
                note(f"  · {name.strip()}")

            # 关键控件是否存在
            for selector, label in (
                ("#target-role", "目标岗位输入框"),
                ("#max-questions", "题目数量输入框"),
            ):
                exists = await page.query_selector(selector)
                note(f"{label}: {'存在' if exists else '缺失!'}")

            # 管理资料链接
            manage = await page.query_selector("a[href^='/projects/']")
            note(f"「管理资料」链接: {'存在' if manage else '缺失!'}")
            if manage:
                note(f"  href={await manage.get_attribute('href')}")

            # ---------------- 资料页 ----------------
            print()
            print("=" * 74)
            print("4. 资料页")
            print("=" * 74)

            # 显式挑一个**有资料的项目**，否则可能落在空项目上，
            # 看不到索引状态与资料行 —— 那样走查就验证不到关键界面。
            target_project_id = None

            links = await page.query_selector_all(
                "a[href^='/projects/']"
            )

            for link in links:
                href = await link.get_attribute("href")
                if not href:
                    continue
                candidate = int(href.rsplit("/", 1)[-1])
                async with AsyncSessionLocal() as db:
                    count = (
                        await db.execute(
                            sql_text(
                                "SELECT COUNT(*) FROM documents "
                                "WHERE project_id = :p"
                            ),
                            {"p": candidate},
                        )
                    ).scalar_one()
                if count:
                    target_project_id = candidate
                    note(
                        f"选择项目 {candidate}（含 {count} 份资料）"
                    )
                    break

            if target_project_id is None:
                note("没有含资料的项目，跳过资料页断言")
            else:
                await page.goto(
                    f"{BASE}/projects/{target_project_id}",
                    wait_until="networkidle",
                )
                await page.wait_for_timeout(2000)

                note(f"URL: {page.url}")

                status_alert = await page.query_selector(".alert")
                if status_alert:
                    note(
                        "索引状态提示: "
                        f"{(await status_alert.text_content()).strip()[:80]}"
                    )

                sel = await page.query_selector("#doc-type")
                if sel:
                    options = await sel.eval_on_selector_all(
                        "option", "els => els.map(e => e.textContent)"
                    )
                    note(f"资料类型选项: {options}")

                file_input = await page.query_selector("#doc-file")
                note(
                    f"文件选择框: {'存在' if file_input else '缺失!'}"
                )

                # 文件选择框的 accept 必须与后端支持的类型一致（D55）
                if file_input:
                    accept = await file_input.get_attribute("accept")
                    note(f"accept 属性: {accept}")

                rows = await page.query_selector_all(
                    ".stack .row-between"
                )
                note(f"已上传资料行数: {len(rows)}")

                # 状态标记：验证 D54 修复（文档级状态是否与 chunk 一致）
                tags = [
                    (await tag.text_content()).strip()
                    for tag in await page.query_selector_all(
                        ".tag"
                    )
                ]
                note(f"资料状态标记: {tags[:8]}")

                await shot(page, "03-project")

            # ---------------- 结束 ----------------
            print()
            print("=" * 74)
            print("5. 面试页与报告页")
            print("=" * 74)

            # 回首页开一场只有 2 题的面试，用来走完"作答 → 报告"
            await page.goto(BASE, wait_until="networkidle")
            await page.wait_for_timeout(1500)

            max_input = await page.query_selector("#max-questions")
            if max_input:
                await max_input.fill("2")

            await page.click("text=开始面试")

            # 生成首题要调模型，给足时间
            try:
                await page.wait_for_url(
                    "**/interviews/**", timeout=180_000
                )
            except Exception as exc:
                note(f"未能进入面试页: {exc}")

            if "/interviews/" in page.url and "report" not in page.url:
                await page.wait_for_timeout(2000)
                note(f"面试页 URL: {page.url}")

                question_box = await page.query_selector(".question-box")
                if question_box:
                    text = (await question_box.text_content()).strip()
                    note(f"当前问题: {text[:90]}")

                tags = await page.query_selector_all(".tag")
                tag_texts = [
                    (await tag.text_content()).strip() for tag in tags
                ]
                note(f"标记: {tag_texts[:6]}")

                await shot(page, "04-interview")

                # 作答：预算 2 题，第 2 次作答后应自动结束并跳报告
                answer = (
                    "我在 MindFlow 项目负责后端开发。简历解析后按 "
                    "500 字符切块、overlap 100，用 text-embedding-v4 "
                    "生成 1024 维向量写入 Milvus，检索时带 project_id "
                    "过滤取 top-5，再按 chunk_id 回 PostgreSQL 取正文。"
                    "选 500 是因为一段项目经历通常 400~600 字。"
                )

                for round_index in (1, 2):
                    area = await page.query_selector("#answer")
                    if not area:
                        note(f"第 {round_index} 轮找不到作答框")
                        break

                    await area.fill(answer)
                    await page.click("text=提交回答")
                    note(f"第 {round_index} 轮已提交，等待分析…")

                    try:
                        await page.wait_for_url(
                            "**/report", timeout=180_000
                        )
                        break
                    except Exception:
                        # 未跳转说明还有追问，继续下一轮
                        await page.wait_for_timeout(3000)

                if "report" in page.url:
                    await page.wait_for_timeout(2500)
                    note(f"报告页 URL: {page.url}")

                    score_values = await page.eval_on_selector_all(
                        ".score-value", "els => els.map(e => e.textContent)"
                    )
                    score_labels = await page.eval_on_selector_all(
                        ".score-label",
                        "els => els.map(e => e.textContent)",
                    )
                    note(f"分数: {list(zip(score_labels, score_values))}")

                    headings = await page.eval_on_selector_all(
                        "h2", "els => els.map(e => e.textContent)"
                    )
                    note(f"报告小节: {headings}")

                    bullets = await page.query_selector_all(
                        ".bullet-list li"
                    )
                    note(f"建议/优势条目数: {len(bullets)}")

                    await shot(page, "05-report")
                else:
                    note(f"未进入报告页，当前 URL: {page.url}")
                    await shot(page, "05-not-report")

            # ---------------- 控制台 ----------------
            print()
            print("=" * 74)
            print("6. 控制台与异常")
            print("=" * 74)
            note(f"控制台错误数: {len(console_errors)}")
            for item in console_errors[:8]:
                note(f"  ! {item[:150]}")
            note(f"页面异常数: {len(page_errors)}")
            for item in page_errors[:8]:
                note(f"  ! {item[:150]}")

            await browser.close()

    finally:
        await restore_password(original_hash)
        print()
        print("  已恢复 mindflow 的原密码哈希")

    # 汇总
    print()
    print("=" * 74)
    print("结果")
    print("=" * 74)
    print(f"  截图目录: {SHOTS}")
    print(f"  控制台错误: {len(console_errors)}")
    print(f"  页面异常: {len(page_errors)}")

    ok = not console_errors and not page_errors

    # 落一份机器可读的结果，便于后续对照
    (SHOTS / "walkthrough.json").write_text(
        json.dumps(
            {
                "console_errors": console_errors,
                "page_errors": page_errors,
                "notes": notes,
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )

    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
