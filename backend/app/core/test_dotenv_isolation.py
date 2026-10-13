"""验证第三方库不会把 `.env` 注入 `os.environ`（§34，D69）。

## 它防的是一个极难排查的真实故障

`pymilvus` 的 `settings.py` 在**导入时**执行无参数的 `load_dotenv()`，
把 `.env` 里的**所有键**写进 `os.environ`（实测 3.0.2）。

而 `os.environ` 的优先级**高于** `pydantic-settings` 读的 `.env` ——
因此注入的值会**覆盖**应用配置。

实测踩到的完整链条：

1. `backend/` 下遗留了一个**五周前的 `.env`**（内容过时）
2. `load_dotenv()` 按**当前工作目录**往上找，于是先找到那个旧文件
3. 它注入了**旧的 `SECRET_KEY`**
4. `check_milvus()` 导入 pymilvus → 污染发生在**门禁进程内**
5. 门禁用 `subprocess` 跑套件 → **子进程继承了被污染的环境**
6. 子进程用旧密钥签发令牌，而运行中的后端用新密钥
7. → 一批套件报 `401`

**为什么它极难排查**：第 7 步的失败信息是
"列表返回 200 status=401" —— 完全指向鉴权，
而真因是"某个第三方库在导入时改了环境变量"。
我为此花了很多轮才定位到。

## 它验证什么

1. **导入 `app.main` 后，没有敏感键被注入 `os.environ`**
2. **在 `check_environment()` 之后启动的子进程，认证仍然正常**
   —— 这一条是端到端的，也是当初真正的失败现象
3. `dotenv.load_dotenv` 确实被替换（而不是碰巧没触发）

用法：uv run python -m app.core.test_dotenv_isolation
"""

import asyncio
import os
import subprocess
import sys

# 这些键一旦出现在"导入后才新增的环境变量"里，就说明污染回来了。
SENSITIVE_KEYS = (
    "SECRET_KEY",
    "DATABASE_URL",
    "REDIS_URL",
    "LLM_API_KEY",
    "EMBEDDING_API_KEY",
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


def main() -> int:
    result = Result()

    print("=" * 78)
    print("dotenv 隔离验证（D69）")
    print("=" * 78)

    # ================================================
    print()
    print("=" * 78)
    print("① 导入 app.main 不得注入敏感环境变量")
    print("=" * 78)

    # 在**子进程**里测，避免本进程已经导入过 pymilvus
    # （那样污染早已发生，测不出"导入时才发生"这件事）。
    probe = """
import os, sys
sys.path.insert(0, r"D:\\yuxi\\MindFlow\\backend")

# 先清掉可能从外层继承的值，确保测的是"导入导致的新增"
for key in %r:
    os.environ.pop(key, None)

before = set(os.environ)

import app.main  # noqa: F401

new = sorted(k for k in os.environ if k not in before)
sensitive = [k for k in new if k in %r]
print("NEW_KEYS=" + ",".join(new))
print("SENSITIVE=" + ",".join(sensitive))
""" % (list(SENSITIVE_KEYS), list(SENSITIVE_KEYS))

    process = subprocess.run(
        [sys.executable, "-c", probe],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )

    output = (process.stdout or "") + (process.stderr or "")

    new_keys: list[str] = []
    sensitive: list[str] = []

    for line in output.splitlines():
        if line.startswith("NEW_KEYS="):
            raw = line.split("=", 1)[1].strip()
            new_keys = [item for item in raw.split(",") if item]
        elif line.startswith("SENSITIVE="):
            raw = line.split("=", 1)[1].strip()
            sensitive = [item for item in raw.split(",") if item]

    print(f"      新增环境变量：{new_keys or '（无）'}")
    print(f"      其中敏感键：{sensitive or '（无）'}")

    result.check(
        "导入 app.main 后没有敏感键被注入",
        not sensitive,
        f"实得 {sensitive}" if sensitive else "无泄漏",
    )

    # ================================================
    print()
    print("=" * 78)
    print("② `dotenv.load_dotenv` 确实被替换")
    print("=" * 78)

    check_probe = """
import sys
sys.path.insert(0, r"D:\\yuxi\\MindFlow\\backend")
from app.core import block_dotenv_pollution  # noqa: F401
import dotenv
name = getattr(dotenv.load_dotenv, "__name__", "")
print("LOAD_DOTENV_NAME=" + name)
"""

    process = subprocess.run(
        [sys.executable, "-c", check_probe],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )

    replaced = False

    for line in ((process.stdout or "") + (process.stderr or "")).splitlines():
        if line.startswith("LOAD_DOTENV_NAME="):
            replaced = line.split("=", 1)[1].strip() == "_noop"

    result.check(
        "load_dotenv 已被替换为 no-op（而不是碰巧没触发）",
        replaced,
        "函数名 _noop" if replaced else "仍指向原实现",
    )

    # ================================================
    print()
    print("=" * 78)
    print("③ 端到端：环境检查之后，子进程的认证仍正常")
    print("=" * 78)

    # 这是**当初真正的失败现象**：
    # `check_environment()` 会导入 pymilvus（经 check_milvus），
    # 于是污染发生在门禁进程内，而 `subprocess` 启动的套件
    # 继承了被污染的环境 → 用旧密钥签发令牌 → 401。
    #
    # 只测 ① 不够：① 测的是"导入时不注入"，
    # 而这里测的是"整条链路下来认证还能用"。
    #
    # 内层直接执行 `probe_child_auth` 模块 —— 不用字符串嵌代码。
    # 第一版把内层代码嵌成字符串，需要四层转义，结果内层没跑起来，
    # 测试报"未取到状态码"：那是**测试自己写错**。
    e2e_probe = """
import asyncio, subprocess, sys
sys.path.insert(0, r"D:\\yuxi\\MindFlow\\backend")
from app.core.test_all import check_environment

asyncio.run(check_environment())

p = subprocess.run(
    [sys.executable, "-m", "app.core.probe_child_auth"],
    capture_output=True, text=True, encoding="utf-8", errors="replace",
    cwd=r"D:\\yuxi\\MindFlow\\backend",
)
sys.stdout.write((p.stdout or "") + (p.stderr or ""))
"""

    process = subprocess.run(
        [sys.executable, "-c", e2e_probe],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=300,
    )

    auth_status = None

    for line in ((process.stdout or "") + (process.stderr or "")).splitlines():
        if line.startswith("AUTH_STATUS="):
            try:
                auth_status = int(line.split("=", 1)[1].strip())
            except ValueError:
                auth_status = None

    result.check(
        "环境检查之后的子进程认证正常（不是 401）",
        auth_status == 200,
        f"HTTP {auth_status}" if auth_status else "未取到状态码",
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
    sys.exit(main())
