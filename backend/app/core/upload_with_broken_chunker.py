"""启动一个**切块器被打坏**的后端，用于验证切块守卫。

正常情况下"有内容却切不出块"走不到（解析器会拒绝纯空白、
`split_text` 对非空内容至少产出 1 块），因此要人为制造。

用法（独立端口，与正常的 8000 不冲突）：

    uv run python -m app.core.upload_with_broken_chunker

然后另一个终端跑：

    uv run python -m app.core.test_chunk_guard
"""

import uvicorn


def main() -> None:
    # 必须在导入 app 之前打补丁，否则路由已经绑定了原始方法
    from app.services.chunking_service import ChunkingService

    # `split_text` 是**同步**的 staticmethod，因此补丁也必须是同步函数。
    # 打成 async 会让调用点拿到 coroutine，报
    # "'coroutine' object is not iterable" —— 那是另一种错误，
    # 验证不到守卫本身。
    def broken_split_text(*_args, **_kwargs):
        """永远返回空 —— 模拟切块逻辑出问题。"""
        return []

    ChunkingService.split_text = staticmethod(broken_split_text)

    print("  已把 ChunkingService.split_text 打成永远返回空")
    print("  启动端口 8098（仅供切块守卫验证）")

    from app.main import app

    uvicorn.run(app, host="127.0.0.1", port=8098, log_level="warning")


if __name__ == "__main__":
    main()
