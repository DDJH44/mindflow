"""验证"长等待提示"在慢响应下的行为（§31.5 第一期验收）。

## 为什么必须专门测慢响应

只测快路径会漏掉这个功能**唯一要解决的问题**：
若请求 1 秒内返回，等待提示根本来不及显示。

因此这个脚本对接的是**打桩的慢后端**（`slow_backend`，
`/answer` 与 `/finish` 各延迟 60 秒），而不是真实后端。

## 前置

```bash
# 终端 1：慢后端（8001）
uv run python -m tests.slow_backend

# 终端 2：前端指向它（5174）
cd web && VITE_BACKEND_ORIGIN=http://127.0.0.1:8001 npx vite --port 5174
```

## 它验证什么

1. 提交后**立即**出现等待提示（而不是转圈一句话）
2. **秒数在涨** —— 这是"界面没卡死"的直接证据
3. 阶段文案**按时间逐条出现**（1s / 5s / 14s）
4. 超过 30 秒出现**超时安抚**
5. 并且显示的是**"已等待 N 秒"**，而不是一个假的百分比

用法：uv run python -m tests.test_waiting_ui
"""

import asyncio
import sys
from pathlib import Path

from playwright.async_api import async_playwright
from sqlalchemy import text

from app.database.session import AsyncSessionLocal

BASE = "http://127.0.0.1:5174"

# 走查用的入口路径（本脚本**不**修改任何密码，
# 它用后端直接签发的 token 注入登录态）。
SHOTS = Path(r"D:\yuxi\MindFlow\docs\ui-shots")

# 用于测试的会话：必须处于可作答状态（asking 且有未答题目）。
# 515 是用户自测时留下的（1 题 0 答），只填一次答案，不删除。
SESSION_ID = 515

ACCOUNT = "mindflow"

results: list[tuple[str, bool]] = []


def record(label: str, ok: bool, extra: str = "") -> None:
    results.append((label, ok))
    suffix = f"  {extra}" if extra else ""
    print(f"  [{'OK  ' if ok else 'FAIL'}] {label}{suffix}")


def find_chromium() -> str | None:
    """复用走查里的实现：找本地已有的 chromium。"""

    import glob

    patterns = [
        r"C:\Users\*\AppData\Local\ms-playwright\chromium-*\chrome-win64\chrome.exe",
        r"C:\Users\*\AppData\Local\ms-playwright\chromium-*\chrome-win\chrome.exe",
    ]

    for pattern in patterns:
        found = glob.glob(pattern)
        if found:
            return found[0]

    return None


async def main() -> int:
    sys.path.insert(0, r"D:\yuxi\MindFlow\backend")

    from app.core.security import create_access_token

    # 确认会话可作答
    async with AsyncSessionLocal() as db:
        # ⚠️ 身份必须取自**会话的所有者**，不能写死账号。
        #
        # 实测踩过：用 `mindflow` 的 token 去访问属于另一个用户的会话，
        # 后端正确地返回 404（所有权隔离），而界面显示
        # "面试会话不存在" —— 看起来像页面结构变了，
        # 实际是测试拿错了身份。
        session_row = (
            await db.execute(
                text(
                    "SELECT status, questions_asked, max_questions, "
                    "user_id "
                    "FROM interview_sessions WHERE id = :s"
                ),
                {"s": SESSION_ID},
            )
        ).first()

        if session_row is None:
            print(f"  会话 {SESSION_ID} 不存在")
            return 1

        user_id = session_row[3]

        answered = (
            await db.execute(
                text(
                    "SELECT COUNT(*) FROM interview_answers a "
                    "JOIN interview_questions q ON q.id = a.question_id "
                    "WHERE q.session_id = :s"
                ),
                {"s": SESSION_ID},
            )
        ).scalar_one()

    print(f"  会话 {SESSION_ID}: status={session_row[0]} "
          f"已问={session_row[1]} 已答={answered}")

    if session_row[0] != "asking" or answered > 0:
        print(
            "  ⚠️ 该会话不适合测试（需要 asking 且未作答）。"
            "请另选一个会话并改 SESSION_ID。"
        )
        return 1

    token = create_access_token({"sub": str(user_id)})

    executable = find_chromium()

    if not executable:
        print("  找不到 chromium，请先 uv run playwright install chromium")
        return 1

    async with async_playwright() as playwright:
        browser = await playwright.chromium.launch(
            executable_path=executable,
            headless=True,
        )
        context = await browser.new_context(
            viewport={"width": 1280, "height": 900}
        )

        # 注入登录态：不修改密码，避免影响其它脚本与真实使用。
        #
        # key 必须与 `api/http.ts` 的 `TOKEN_STORAGE_KEY` 一致 ——
        # 写错会让页面跳回登录页，而失败信息看起来像"页面结构变了"。
        await context.add_init_script(
            f"""
            window.localStorage.setItem('mindflow.token', {token!r});
            """
        )

        page = await context.new_page()

        console_errors: list[str] = []
        page.on(
            "console",
            lambda message: (
                console_errors.append(message.text)
                if message.type == "error"
                else None
            ),
        )
        page.on("pageerror", lambda exc: console_errors.append(str(exc)))

        try:
            await page.goto(
                f"{BASE}/interviews/{SESSION_ID}",
                wait_until="networkidle",
            )
            await page.wait_for_timeout(2500)

            print(f"  URL: {page.url}")

            # 填入回答并提交
            textarea = await page.query_selector("#answer")

            if textarea is None:
                print("  找不到回答输入框 —— 页面可能不在可作答状态")
                print(f"  页面片段：{(await page.text_content('body'))[:200]}")
                return 1

            await textarea.fill(
                "这是等待提示的验证回答。我会说明具体做法、"
                "参数取值与选择理由，用来触发一次真实的模型调用。"
            )

            # ⚠️ 页面上有**两个** `.btn-primary`（"结束并生成报告"
            # 与"提交回答"），而前者在 DOM 里更靠前、且处于 disabled。
            # 用 `button.btn-primary` 会选到它并一直等到超时 ——
            # 而失败信息看起来像"按钮点不动"。
            #
            # 因此用文字精确定位提交按钮。
            submit_button = page.locator(
                "button.btn-primary", has_text="提交回答"
            )

            await submit_button.click()

            # ================================================
            print()
            print("=" * 74)
            print("1. 提交后立即出现等待提示")
            print("=" * 74)

            await page.wait_for_timeout(1200)

            waiting = await page.query_selector(".waiting")
            record(
                "出现 .waiting 等待提示",
                waiting is not None,
            )

            if waiting is None:
                print("      页面片段：", (await page.text_content("body"))[:300])
                return 1

            body_early = await waiting.text_content()
            record(
                "显示了已等待秒数",
                "已等待" in body_early,
                f"{(body_early or '').strip()[:60]}",
            )
            # 断言要针对**具体的反模式**，而不是查某个字。
            # 起初写的是 `"步" not in body_early`，结果被
            # "这一步需要调用模型"里的正常中文误判 ——
            # 那种断言会在无关的文案改动上随机失败。
            import re as _re

            has_step_counter = bool(
                _re.search(r"第\s*\d+\s*/\s*\d+\s*步", body_early)
            )
            record(
                "**没有**显示假的进度（百分比或「第 N/M 步」）",
                "%" not in body_early and not has_step_counter,
                "不假装知道服务端进度",
            )

            # ================================================
            print()
            print("=" * 74)
            print("2. 秒数在涨（界面没卡死的直接证据）")
            print("=" * 74)

            first_text = (await waiting.text_content()) or ""

            await page.wait_for_timeout(4000)

            second_text = (await waiting.text_content()) or ""

            def seconds_of(raw: str) -> int:
                import re

                match = re.search(r"已等待\s*(\d+)\s*秒", raw)
                return int(match.group(1)) if match else -1

            before = seconds_of(first_text)
            after = seconds_of(second_text)

            record(
                "秒数随时间递增",
                before >= 0 and after > before,
                f"{before}s → {after}s",
            )

            # ================================================
            print()
            print("=" * 74)
            print("3. 说明了这一步要做什么，但不假装知道做到哪了")
            print("=" * 74)

            steps_text = await page.text_content(".waiting-steps")

            record(
                "列出了这一步将要做的事",
                bool(steps_text and "→" in steps_text),
                f"{(steps_text or '').strip()[:70]}",
            )

            record(
                "有不确定进度条（流动而不是按百分比填充）",
                bool(await page.query_selector(".waiting-track-fill")),
            )

            # ⚠️ **动画是否真的在动，这个脚本测不到。**
            #
            # 截图是单帧，`getComputedStyle` 能读到 `animation` 属性
            # 却读不到"它正在播"。因此这里只能断言**动画被声明了** ——
            # 这比不测强，但**不等于**验证了视觉流畅度。
            # 真实的动画效果只能靠人眼看，这一点如实记录在这里，
            # 避免以后误以为它已被自动覆盖。
            animation = await page.eval_on_selector(
                ".waiting-track-fill",
                "el => getComputedStyle(el).animationName",
            )
            record(
                "进度条声明了动画（**不等于**验证了画面流畅）",
                animation not in ("none", ""),
                f"animation-name={animation}",
            )

            # 这一条是**设计意图的回归**：第一版把阶段做成
            # 逐条打勾来模拟进度，截图复核时发现它在 32 秒时
            # 就显示"正在准备下一个问题"，而那时模型多半还在分析。
            # 那等于把估算当成事实，因此改成不声称到达了哪一步。
            record(
                "没有任何「已完成」标记（不声称到达了哪一步）",
                not await page.query_selector(".waiting-stages li"),
            )

            # ================================================
            print()
            print("=" * 74)
            print("4. 超过 30 秒出现超时安抚")
            print("=" * 74)

            # 提交后已过约 6 秒；再等到 32 秒以上
            await page.wait_for_timeout(27000)

            reassure = await page.query_selector(".waiting-reassure")
            elapsed_now = seconds_of(
                (await waiting.text_content()) or ""
            )

            record(
                "超过阈值后出现超时安抚",
                reassure is not None,
                f"当前 {elapsed_now}s",
            )

            if reassure:
                # 变量名不能叫 `text` —— 那会遮蔽模块级的
                # `sqlalchemy.text`，让上面的查询抛 UnboundLocalError。
                reassure_text = " ".join(
                    ((await reassure.text_content()) or "").split()
                )
                print(f"      安抚文案：{reassure_text[:80]}")

            await page.screenshot(path=str(SHOTS / "07-waiting.png"))
            print(f"      截图 07-waiting.png")

            # ================================================
            print()
            print("=" * 74)
            print("5. 控制台健康度")
            print("=" * 74)

            record(
                "无控制台错误",
                not console_errors,
                f"{len(console_errors)} 条"
                + (f"：{console_errors[0][:60]}" if console_errors else ""),
            )

        finally:
            await browser.close()

    print()
    print("=" * 74)
    passed = sum(1 for _, ok in results if ok)
    failed = [label for label, ok in results if not ok]
    print(f"通过 {passed} / {len(results)}")
    if failed:
        print("失败项:")
        for label in failed:
            print("  -", label)
        return 1

    print("全部通过")
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
