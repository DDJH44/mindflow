"""验证：在 `check_environment()` 之后启动的子进程，认证仍然正常。

## 这是当初真正的失败现象

门禁的 `check_environment()` 会导入 pymilvus（经 `check_milvus`），
而 pymilvus 的 `load_dotenv()` 会污染 `os.environ`。
门禁随后用 `subprocess` 启动套件 —— **子进程继承了被污染的环境**，
于是它用**旧密钥**签发令牌，而运行中的后端用新密钥 → 401。

失败信息是"列表返回 200 status=401"，完全指向鉴权，
而真因是"某个第三方库在导入时改了环境变量"。

## 为什么单独放在一个文件里

第一版把这段代码用字符串嵌在 `test_dotenv_isolation.py` 里，
需要四层转义（外层的 `subprocess` → 探针 → 内层 `subprocess` → 内层代码），
结果**内层探针没能运行**，测试报"未取到状态码" ——
那是**测试自己写错**，而不是被守护的代码有问题。

拆成独立文件后，内层 `subprocess` 直接执行本文件，
没有任何转义问题。

用法（一般由 test_dotenv_isolation 调用）：
    uv run python -m app.core.probe_child_auth
"""

import asyncio
import sys

import httpx
from sqlalchemy import text

from app.database.session import AsyncSessionLocal

BASE = "http://127.0.0.1:5173/api"


async def main() -> int:
    sys.path.insert(0, r"D:\yuxi\MindFlow\backend")

    from app.core.security import create_access_token

    async with AsyncSessionLocal() as db:
        user_id = (
            await db.execute(
                text("SELECT id FROM users WHERE username = :u"),
                {"u": "mindflow"},
            )
        ).scalar_one()

    token = create_access_token({"sub": str(user_id)})

    async with httpx.AsyncClient(
        base_url=BASE,
        headers={"Authorization": f"Bearer {token}"},
        timeout=60.0,
    ) as client:
        response = await client.get("/interviews")

    # 这一行是给调用方解析的**机器可读**输出
    print(f"AUTH_STATUS={response.status_code}")
    print(f"AUTH_DETAIL={str(response.text)[:80]}")

    return 0 if response.status_code == 200 else 1


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
