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
import uuid
from pathlib import Path

from playwright.async_api import async_playwright

sys.path.insert(0, r"D:\yuxi\MindFlow\backend")

from sqlalchemy import text as sql_text  # noqa: E402

from app.database.session import AsyncSessionLocal  # noqa: E402

BASE = "http://127.0.0.1:5173"
SHOTS = Path(r"D:\yuxi\MindFlow\docs\ui-shots")
TEMP_PASSWORD = "temp_ui_walk_123"

# 走查用的账号。它在本项目里就是跑验证的账号，
# 因此清理时把它名下的会话当作测试数据。
ACCOUNT = "mindflow"

# 走查清理时**要保留**的会话 id（历史遗留数据）。
# 若要保留 mindflow 名下某场自测面试，把它的 id 加进来。
KEEP_SESSION_IDS = (1, 2)

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


async def cleanup_session(session_id: int | None) -> None:
    """删除走查造的会话及其关联数据。"""

    if not session_id:
        return

    try:
        async with AsyncSessionLocal() as db:
            await db.execute(
                sql_text(
                    "DELETE FROM interview_answers "
                    "WHERE question_id IN "
                    "(SELECT id FROM interview_questions "
                    " WHERE session_id = :s)"
                ),
                {"s": session_id},
            )
            await db.execute(
                sql_text(
                    "DELETE FROM interview_questions "
                    "WHERE session_id = :s"
                ),
                {"s": session_id},
            )
            await db.execute(
                sql_text(
                    "DELETE FROM interview_status_history "
                    "WHERE session_id = :s"
                ),
                {"s": session_id},
            )
            await db.execute(
                sql_text(
                    "DELETE FROM interview_sessions WHERE id = :s"
                ),
                {"s": session_id},
            )
            await db.commit()
    except Exception as exc:  # noqa: BLE001
        print(f"      清理会话 {session_id} 失败: {exc}")


async def seed_unfinished_session() -> int | None:
    """造一场"未完成"的面试，供历史页断言。

    为什么需要：默认视图是"只看未完成"，若库里恰好没有未完成
    的会话，历史页会是空列表 —— 走查就验证不到列表项、
    状态标记与跳转，等于这一页没被检查过。
    """

    try:
        async with AsyncSessionLocal() as db:
            user_id = (
                await db.execute(
                    sql_text(
                        "SELECT id FROM users WHERE username = :u"
                    ),
                    {"u": ACCOUNT},
                )
            ).scalar_one()

            project_id = (
                await db.execute(
                    sql_text(
                        "SELECT id FROM projects "
                        "WHERE owner_id = :u ORDER BY id LIMIT 1"
                    ),
                    {"u": user_id},
                )
            ).scalar_one_or_none()

            if project_id is None:
                return None

            session_id = (
                await db.execute(
                    sql_text(
                        "INSERT INTO interview_sessions "
                        "(project_id, user_id, status, interview_type, "
                        " target_role, current_question_index, "
                        " max_questions, questions_asked, "
                        " created_at, updated_at) "
                        "VALUES (:p, :u, 'asking', 'technical', "
                        " '后端工程师', 1, 8, 1, now(), now()) "
                        "RETURNING id"
                    ),
                    {"p": project_id, "u": user_id},
                )
            ).scalar_one()

            # 一道已作答的题，让"已作答 1 / 8"有真实数据
            question_id = (
                await db.execute(
                    sql_text(
                        "INSERT INTO interview_questions "
                        "(session_id, question, question_type, "
                        " question_index, context, evidence_chunk_ids, "
                        " is_general, created_at) "
                        "VALUES (:s, :q, 'technical', 0, NULL, "
                        " '{}', true, now()) RETURNING id"
                    ),
                    {
                        "s": session_id,
                        "q": "走查用的问题：介绍一下你的项目经历。",
                    },
                )
            ).scalar_one()

            await db.execute(
                sql_text(
                    "INSERT INTO interview_answers "
                    "(question_id, answer, created_at) "
                    "VALUES (:q, '走查用的回答。', now())"
                ),
                {"q": question_id},
            )

            await db.commit()

        return session_id

    except Exception as exc:  # noqa: BLE001
        print(f"      造会话失败: {exc}")
        return None


async def cleanup_stale_walkthrough_sessions() -> list[int]:
    """清理 `mindflow` 名下遗留的会话，保留历史数据。

    保留规则只有一条：**会话 1、2**（`created` 状态的历史遗留数据）。

    为什么把 `completed` 也清掉：走查为了截图会跑完一整场面试，
    结束时那场是 `completed`。若只清未完成状态，它们会一场场累积
    （实测累积了 3 场），而历史页是**用户能看到的界面**，
    堆着测试数据会让人以为功能坏了。

    为什么不会误删真实数据：走查固定用 `mindflow` 账号，
    而这个账号在测试环境里就是用来跑验证的；
    真实用户（如 `不开挖机`）的会话按 `user_id` 隔离，不会被碰。
    若要保留 `mindflow` 名下的某场，把 id 加进 `KEEP_SESSION_IDS`。
    """

    try:
        async with AsyncSessionLocal() as db:
            user_id = (
                await db.execute(
                    sql_text(
                        "SELECT id FROM users WHERE username = :u"
                    ),
                    {"u": ACCOUNT},
                )
            ).scalar_one()

            stale = list(
                (
                    await db.execute(
                        sql_text(
                            "SELECT id FROM interview_sessions "
                            "WHERE user_id = :u "
                            "  AND id <> ALL(:keep) "
                            "ORDER BY id"
                        ),
                        {"u": user_id, "keep": list(KEEP_SESSION_IDS)},
                    )
                ).scalars().all()
            )

        for session_id in stale:
            await cleanup_session(session_id)

        return stale

    except Exception as exc:  # noqa: BLE001
        print(f"      清理遗留会话失败: {type(exc).__name__}: {exc}")
        return []


async def purge_orphan_vectors() -> None:
    """清掉已无对应 chunk 的 Milvus 向量。"""

    try:
        from app.core.cleanup_test_users import purge_orphan_vectors as run

        purged = await run()
        if purged:
            print(f"  已清理孤儿向量 {purged} 个")
    except Exception:  # noqa: BLE001
        # 没有 Milvus 时不该让走查失败
        pass


async def seed_profile_evaluations(
    count: int = 3,
) -> tuple[list[int], int | None]:
    """造若干带评价的已完成面试，供能力画像页断言。

    返回 (session_ids, project_id)。

    为什么要造：画像页在样本不足（<3 场）时**故意不显示**
    维度卡片与重复弱点 —— 那是正确行为，但走查就看不到那些界面。
    因此要造够样本才能验证它们渲染正确。
    """

    session_ids: list[int] = []
    project_id = None

    try:
        async with AsyncSessionLocal() as db:
            user_id = (
                await db.execute(
                    sql_text(
                        "SELECT id FROM users WHERE username = :u"
                    ),
                    {"u": ACCOUNT},
                )
            ).scalar_one()

            project_id = (
                await db.execute(
                    sql_text(
                        "INSERT INTO projects "
                        "(name, description, owner_id, status, "
                        " created_at, updated_at) "
                        "VALUES (:n, NULL, :u, 'active', "
                        " now(), now()) RETURNING id"
                    ),
                    {
                        "n": f"画像走查 {uuid.uuid4().hex[:6]}",
                        "u": user_id,
                    },
                )
            ).scalar_one()

            # 分数刻意有跨度（含一次高、一次低），
            # 这样极差警示与趋势图都会被触发到。
            score_sets = [
                {
                    "overall_score": 90,
                    "technical_score": 90,
                    "project_score": 70,
                    "communication_score": 70,
                },
                {
                    "overall_score": 40,
                    "technical_score": 40,
                    "project_score": 40,
                    "communication_score": 40,
                },
                {
                    "overall_score": 70,
                    "technical_score": 70,
                    "project_score": 70,
                    "communication_score": 70,
                },
            ]

            for index in range(count):
                scores = score_sets[index % len(score_sets)]

                session_id = (
                    await db.execute(
                        sql_text(
                            "INSERT INTO interview_sessions "
                            "(project_id, user_id, status, "
                            " interview_type, target_role, "
                            " current_question_index, max_questions, "
                            " questions_asked, termination_reason, "
                            " created_at, updated_at) "
                            "VALUES (:p, :u, 'completed', 'technical', "
                            " '后端工程师', 2, 2, 2, "
                            " 'budget_exhausted', now(), now()) "
                            "RETURNING id"
                        ),
                        {"p": project_id, "u": user_id},
                    )
                ).scalar_one()

                session_ids.append(session_id)

                # 两条弱点原文相同，触发"反复出现的问题"
                await db.execute(
                    sql_text(
                        "INSERT INTO interview_evaluations "
                        "(session_id, overall_score, technical_score, "
                        " project_score, communication_score, feedback, "
                        " strengths, weaknesses, suggestions, "
                        " scoring_details, created_at) "
                        "VALUES (:s, :o, :t, :p, :c, '走查用反馈', "
                        " CAST(:st AS JSON), CAST(:wk AS JSON), "
                        " CAST(:sg AS JSON), CAST(:sd AS JSON), now())"
                    ),
                    {
                        "s": session_id,
                        "o": scores["overall_score"],
                        "t": scores["technical_score"],
                        "p": scores["project_score"],
                        "c": scores["communication_score"],
                        "st": json.dumps(["能说清基础参数"]),
                        "wk": json.dumps(
                            [
                                "走查用重复弱点：未说明 TTL 设置依据",
                                f"走查用第 {index} 条独立弱点",
                            ]
                        ),
                        "sg": json.dumps(["走查用建议"]),
                        "sd": json.dumps({}),
                    },
                )

            await db.commit()

    except Exception as exc:  # noqa: BLE001
        print(f"      造画像样本失败: {type(exc).__name__}: {exc}")
        return [], None

    return session_ids, project_id


async def cleanup_profile_samples(
    session_ids: list[int],
    project_id: int | None,
) -> None:
    """清理走查为画像页造的数据。"""

    try:
        async with AsyncSessionLocal() as db:
            if session_ids:
                await db.execute(
                    sql_text(
                        "DELETE FROM interview_evaluations "
                        "WHERE session_id = ANY(:s)"
                    ),
                    {"s": session_ids},
                )
                await db.execute(
                    sql_text(
                        "DELETE FROM interview_status_history "
                        "WHERE session_id = ANY(:s)"
                    ),
                    {"s": session_ids},
                )
                await db.execute(
                    sql_text(
                        "DELETE FROM interview_sessions "
                        "WHERE id = ANY(:s)"
                    ),
                    {"s": session_ids},
                )

            if project_id:
                await db.execute(
                    sql_text("DELETE FROM projects WHERE id = :p"),
                    {"p": project_id},
                )

            await db.commit()
    except Exception as exc:  # noqa: BLE001
        print(f"      清理画像样本失败: {exc}")


async def seed_index_document() -> tuple[int | None, int | None]:
    """造一个项目 + 一份资料，用于走查异步索引的界面行为。

    返回 (project_id, document_id)。

    用**小文件**：大文件让走查等太久，而这里要验证的是界面的
    轮询行为，与文件大小无关。

    为什么必须造：走查原先只挑"已有资料的项目"，而开发库里
    `mindflow` 名下一个含资料的项目都没有 —— 于是资料页与轮询
    一直被跳过、从未被覆盖。
    """

    try:
        async with AsyncSessionLocal() as db:
            user_id = (
                await db.execute(
                    sql_text(
                        "SELECT id FROM users WHERE username = :u"
                    ),
                    {"u": ACCOUNT},
                )
            ).scalar_one()

        import httpx

        from app.core.security import create_access_token

        token = create_access_token({"sub": str(user_id)})

        async with httpx.AsyncClient(
            base_url=f"{BASE}/api",
            headers={"Authorization": f"Bearer {token}"},
            timeout=120.0,
        ) as client:
            project_id = (
                await client.post(
                    "/projects",
                    json={
                        "name": f"索引走查 {uuid.uuid4().hex[:6]}"
                    },
                )
            ).json()["id"]

            response = await client.post(
                f"/projects/{project_id}/documents",
                params={"document_type": "resume"},
                files={
                    "file": (
                        "walkthrough_index.txt",
                        (
                            "这是一份用于界面走查的资料。"
                            "它包含 RAG 检索与 Milvus 向量库的描述。"
                        ).encode("utf-8"),
                        "text/plain",
                    )
                },
            )

            document_id = (
                response.json().get("id")
                if response.status_code == 202
                else None
            )

        return project_id, document_id

    except Exception as exc:  # noqa: BLE001
        print(f"      造索引走查资料失败: {type(exc).__name__}: {exc}")
        return None, None


async def cleanup_index_document(
    project_id: int | None,
    document_id: int | None,
) -> None:
    """清理走查为异步索引造的项目与资料。

    直接删库**同时删向量**：走查造的文档可能已经索引完成，
    只删库会留下孤儿向量（D52）。
    """

    if not project_id:
        return

    try:
        from app.services.milvus_vector_store import MilvusVectorStore

        async with AsyncSessionLocal() as db:
            if document_id:
                chunk_ids = [
                    row[0]
                    for row in (
                        await db.execute(
                            sql_text(
                                "SELECT id FROM document_chunks "
                                "WHERE document_id = :d"
                            ),
                            {"d": document_id},
                        )
                    ).all()
                ]

                if chunk_ids:
                    try:
                        await MilvusVectorStore().delete(ids=chunk_ids)
                    except Exception as exc:  # noqa: BLE001
                        print(f"      清理向量失败: {exc}")

            await db.execute(
                sql_text(
                    "DELETE FROM document_index_jobs "
                    "WHERE document_id IN ("
                    "  SELECT id FROM documents WHERE project_id = :p)"
                ),
                {"p": project_id},
            )
            await db.execute(
                sql_text("DELETE FROM projects WHERE id = :p"),
                {"p": project_id},
            )
            await db.commit()

    except Exception as exc:  # noqa: BLE001
        print(f"      清理索引走查数据失败: {exc}")


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

    # 在 try 之外初始化：finally 里要用它清理走查创建的会话，
    # 定义在 try 内会在异常路径上拿不到。
    walkthrough_session_id: int | None = None

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

            # ---------------- 历史页 ----------------
            print()
            print("=" * 74)
            print("4. 面试历史页")
            print("=" * 74)

            # 造一场"未完成"的面试，否则"只看未完成"会是空列表，
            # 验证不到列表项与操作文案。
            seeded_session_id = await seed_unfinished_session()
            note(f"造了一场未完成会话 {seeded_session_id}")

            try:
                await page.goto(
                    f"{BASE}/history", wait_until="networkidle"
                )
                await page.wait_for_timeout(2000)

                note(f"URL: {page.url}")
                heading = await page.text_content("h1")
                note(f"主标题: {heading}")

                # 默认应只看未完成
                filter_button = await page.text_content(
                    ".card button.btn-ghost"
                )
                note(f"筛选按钮文案: {filter_button.strip()}")

                summary = await page.text_content(".card .muted")
                note(f"汇总: {' '.join(summary.split())}")

                rows = await page.query_selector_all(".list-item")
                note(f"列表行数: {len(rows)}")

                if rows:
                    first = " ".join(
                        (await rows[0].text_content()).split()
                    )
                    note(f"首行: {first[:110]}")

                    tags = [
                        (await tag.text_content()).strip()
                        for tag in await rows[0].query_selector_all(
                            ".tag"
                        )
                    ]
                    note(f"首行标记: {tags}")

                    # 每行都应有可点的动作文案。
                    #
                    # 这条是**看截图才发现**的：早期只打印行数，
                    # 而"已作答 3 题却显示 —（打不开）"这种问题
                    # 数字上完全看不出来。因此显式断言动作存在。
                    actionless = []
                    for index, row in enumerate(rows):
                        text = " ".join(
                            (await row.text_content()).split()
                        )
                        if not any(
                            word in text
                            for word in ("回到面试", "继续面试", "看报告")
                        ):
                            actionless.append(index)

                    note(
                        f"缺少可点动作的行: "
                        f"{actionless if actionless else '无'}"
                    )

                await shot(page, "04-history")

                # 切到"查看全部"，应包含已完成的会话
                await page.click(".card button.btn-ghost")
                await page.wait_for_timeout(1800)

                all_rows = await page.query_selector_all(".list-item")
                all_tags = []
                for row in all_rows[:6]:
                    for tag in await row.query_selector_all(".tag"):
                        all_tags.append(
                            (await tag.text_content()).strip()
                        )
                note(f"「查看全部」行数: {len(all_rows)}")
                note(f"出现的状态标记: {sorted(set(all_tags))}")

                # 点进未完成的会话应回到面试页
                if all_rows:
                    await all_rows[0].click()
                    await page.wait_for_timeout(2500)
                    note(f"点击首行后 URL: {page.url}")

            finally:
                await cleanup_session(seeded_session_id)

            # ---------------- 能力画像页 ----------------
            print()
            print("=" * 74)
            print("5. 能力画像页")
            print("=" * 74)

            profile_sessions: list[int] = []
            profile_project_id = None

            try:
                (
                    profile_sessions,
                    profile_project_id,
                ) = await seed_profile_evaluations(count=3)
                note(
                    f"造了 {len(profile_sessions)} 场带评价的面试"
                )

                await page.goto(
                    f"{BASE}/profile", wait_until="networkidle"
                )
                await page.wait_for_timeout(2500)

                note(f"URL: {page.url}")
                heading = await page.text_content("h1")
                note(f"主标题: {heading}")

                # 解读说明必须在最前面 —— 用户会先看分数再看结论
                warnings = await page.query_selector_all(
                    ".alert-warn"
                )
                note(f"解读说明条数: {len(warnings)}")
                if warnings:
                    first_warning = " ".join(
                        (await warnings[0].text_content()).split()
                    )
                    note(f"第一条说明: {first_warning[:90]}")

                cells = await page.query_selector_all(
                    ".dimension-cell"
                )
                note(f"维度卡片数: {len(cells)}")

                if cells:
                    first_cell = " ".join(
                        (await cells[0].text_content()).split()
                    )
                    note(f"第一张卡片: {first_cell[:100]}")

                # 趋势图必须真的画出来（单点也要有点）
                sparks = await page.query_selector_all(".sparkline")
                note(f"趋势图数量: {len(sparks)}")
                if sparks:
                    polylines = await sparks[0].query_selector_all(
                        "polyline"
                    )
                    circles = await sparks[0].query_selector_all(
                        "circle"
                    )
                    note(
                        f"第一张趋势图: 折线 {len(polylines)} 条、"
                        f"数据点 {len(circles)} 个"
                    )

                recurring = await page.query_selector_all(
                    ".bullet-list li"
                )
                note(f"列表条目数: {len(recurring)}")

                rows = await page.query_selector_all(
                    ".data-table tbody tr"
                )
                note(f"逐场明细行数: {len(rows)}")

                await shot(page, "05-profile")

            finally:
                await cleanup_profile_samples(
                    profile_sessions, profile_project_id
                )

            # ---------------- 异步索引（§28）----------------
            print()
            print("=" * 74)
            print("6. 资料上传与异步索引")
            print("=" * 74)

            # 造一个项目并上传一份资料，专门验证异步索引的**界面行为**。
            #
            # 为什么必须造：走查原先只挑"已有资料的项目"，而开发库里
            # `mindflow` 名下一个含资料的项目都没有 —— 于是这一步
            # 一直被跳过，资料页与轮询**从未被走查覆盖**。
            # 加了轮询之后更不能跳过：没有它，用户看到的是一个
            # 永远不变的"已切块·未索引"。
            index_project_id, index_document_id = (
                await seed_index_document()
            )

            try:
                if index_project_id is None:
                    note("造资料失败，跳过异步索引走查")
                else:
                    await page.goto(
                        f"{BASE}/projects/{index_project_id}",
                        wait_until="networkidle",
                    )

                    # 立刻检查：应当看到"正在建立向量索引"，
                    # 以及文档行上的"已切块·未索引"。
                    #
                    # 这一步有时间敏感性 —— 小文件可能在几百毫秒内
                    # 就索引完了。因此这里**不断言**"一定在索引中"，
                    # 只记录观察到的状态（见下方 note）。
                    await page.wait_for_timeout(400)

                    pending_hint = await page.query_selector(
                        ".spinner"
                    )
                    note(
                        "上传后立即出现的进度提示: "
                        f"{'有' if pending_hint else '无（可能已索引完）'}"
                    )

                    row_text = " ".join(
                        (
                            await page.text_content(".card")
                        ).split()
                    )
                    note(f"资料区文案: {row_text[:150]}")

                    # 等轮询把它变成"已索引·可检索"。
                    #
                    # 这是本步的核心断言：前端轮询必须真的能
                    # 把状态刷新过来，否则用户会以为上传坏了。
                    indexed = False
                    for _ in range(40):
                        body = await page.text_content("body")

                        if "已索引" in body:
                            indexed = True
                            break

                        await page.wait_for_timeout(1000)

                    note(
                        "轮询后出现「已索引·可检索」: "
                        f"{indexed}"
                    )

                    if not indexed:
                        print(
                            "      ⚠️ 轮询未能在 40 秒内刷新出已索引状态"
                        )

                    await shot(page, "06-project-indexed")

            finally:
                await cleanup_index_document(
                    index_project_id, index_document_id
                )

            # ---------------- 资料页 ----------------
            print()
            print("=" * 74)
            print("7. 资料页")
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
            print("8. 面试页与报告页")
            print("=" * 74)

            # 回首页开一场只有 2 题的面试，用来走完"作答 → 报告"
            await page.goto(BASE, wait_until="networkidle")
            await page.wait_for_timeout(1500)

            max_input = await page.query_selector("#max-questions")
            if max_input:
                await max_input.fill("2")

            await page.click("text=开始面试")

            # 记录这次走查**真实创建**的会话，结束时删掉。
            #
            # 不加这一步的话每跑一次走查就在库里留一场面试
            # （实测累积到 11 场，历史页被测试数据淹没）。
            # 这类脚本必须自己收尾。
            #
            # 生成首题要调模型，给足时间
            try:
                await page.wait_for_url(
                    "**/interviews/**", timeout=180_000
                )
            except Exception as exc:
                note(f"未能进入面试页: {exc}")

            if "/interviews/" in page.url:
                # 形如 /interviews/123 或 /interviews/123/report
                parts = [
                    p
                    for p in page.url.split("/")
                    if p and p != "report"
                ]
                try:
                    walkthrough_session_id = int(parts[-1])
                except (ValueError, IndexError):
                    walkthrough_session_id = None
                note(f"走查会话 id={walkthrough_session_id}")

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

                # 点开资料依据。
                #
                # 只断言"N 段"这个数字存在是不够的 ——
                # 那只能证明标签渲染了，证明不了**点开能看到内容**，
                # 而"依据可追溯"（§9.3）恰恰要求后者。
                toggle = await page.query_selector(".evidence-toggle")
                if toggle is None:
                    note("资料依据入口: 缺失（可能是通用题）")
                else:
                    note(
                        "资料依据入口: "
                        f"'{(await toggle.text_content()).strip()}'"
                    )
                    await toggle.click()
                    await page.wait_for_timeout(1500)

                    items = await page.query_selector_all(
                        ".evidence-item"
                    )
                    note(f"展开后依据条数: {len(items)}")

                    if items:
                        first_text = " ".join(
                            (await items[0].text_content()).split()
                        )
                        note(f"第一条依据: {first_text[:100]}")

                    # 展开态必须真的有正文，否则等于没展开
                    body = await page.query_selector(".evidence-text")
                    note(
                        "依据正文可读: "
                        f"{bool(body and (await body.text_content()).strip())}"
                    )

                    await shot(page, "04-interview-evidence")

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

                    # 报告页也要能点开依据 —— 这是"答案有据可查"的
                    # 最后落点，与面试页是同一套判定。
                    report_toggles = await page.query_selector_all(
                        ".evidence-toggle"
                    )
                    note(f"报告页证据入口数: {len(report_toggles)}")

                    if report_toggles:
                        await report_toggles[0].click()
                        await page.wait_for_timeout(1500)

                        report_items = await page.query_selector_all(
                            ".evidence-item"
                        )
                        note(f"报告页展开后依据条数: {len(report_items)}")

                        report_body = await page.query_selector(
                            ".evidence-text"
                        )
                        note(
                            "报告页依据正文可读: "
                            f"{bool(report_body and (await report_body.text_content()).strip())}"
                        )

                        await shot(page, "05-report-evidence")

                    await shot(page, "05-report")
                else:
                    note(f"未进入报告页，当前 URL: {page.url}")
                    await shot(page, "05-not-report")

            # ---------------- 控制台 ----------------
            print()
            print("=" * 74)
            print("9. 控制台与异常")
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

        # 删掉走查真实创建的会话。
        #
        # 走查为了截图会开一场真面试（消耗一次额度），
        # 不清理就会在库里不断累积 —— 而这是**用户能看到的**
        # 历史列表，堆满测试数据会让人以为功能坏了。
        if walkthrough_session_id:
            await cleanup_session(walkthrough_session_id)
            print(f"  已清理走查会话 {walkthrough_session_id}")

        # 顺带清掉其它被中断的运行留下的会话与孤儿向量。
        #
        # 只清本账号 + 明确是测试产生的会话：保留会话 1、2
        # （历史遗留数据）与其它用户的会话。
        cleaned = await cleanup_stale_walkthrough_sessions()
        if cleaned:
            print(f"  已清理遗留会话 {cleaned}")

        await purge_orphan_vectors()

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
