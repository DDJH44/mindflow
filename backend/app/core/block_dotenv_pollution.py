"""阻止第三方库把 `.env` 注入 `os.environ`（安全修复）。

## 问题：`pymilvus` 会污染进程环境

`pymilvus` 的 `settings.py` 在**模块导入时**执行：

```python
from dotenv import load_dotenv
load_dotenv()          # ← 无参数：从当前工作目录往上找 .env
```

`load_dotenv()` 的行为是**把 `.env` 里所有键写进 `os.environ`**。

实测（pymilvus 3.0.2）它注入了这些：

```
DATABASE_URL, REDIS_URL, SECRET_KEY, ALGORITHM,
ACCESS_TOKEN_EXPIRE_MINUTES, LLM_API_KEY, LLM_BASE_URL, LLM_MODEL,
EMBEDDING_API_KEY, ...
```

## 为什么这很危险（三个后果，都实测过）

**① 子进程会继承它们，而 `os.environ` 优先级**高于** `.env`。**

这造成过一次真实的、很难查的故障：`backend/` 下曾遗留一个
**五周前的 `.env`**，而 `load_dotenv()` 按**当前工作目录**往上找 ——
从 `backend/` 启动时它先找到那个旧文件，于是注入了**旧的 SECRET_KEY**。

之后 `app.core.config` 读到 `os.environ` 里已有的值就不再覆盖，
于是**测试脚本签发的令牌用的旧密钥**，而运行中的后端用新密钥
→ 一批套件报 401，而失败信息完全指向别处（"列表返回 200 status=401"）。

**② 进程环境里多了一把数据库口令、一个 JWT 密钥、若干 API Key。**

任何子进程都继承；崩溃转储、错误上报、CI 日志都可能带上它们。
而这些凭据本来只该存在于一个文件里。

**③ 它让"当前工作目录"变成隐式配置源。**

同一个代码库从不同目录启动会读到不同配置 —— 这是最难排查的
一类问题，因为**失败现象与原因之间没有可见的联系**。

## 为什么只能这样修（而不是换库或改配置）

- **不能改 pymilvus**：那是 `site-packages` 里的第三方代码，
  改动会在下次 `uv sync` 时消失。
- **不能靠"保证工作目录正确"**：那依赖人的自觉，
  而上面那个五周前的遗留文件正说明自觉不可靠。
- **不能靠"删掉多余的 .env"**：那只是消除了这一次的症状；
  库依然会注入，隐患依然在。

因此：**在 pymilvus 被导入之前，把 `dotenv.load_dotenv` 换成空操作。**
本项目**不需要**它做这件事 —— `pydantic-settings` 已经从
一个**明确的路径**（`BASE_DIR / ".env"`）读取配置，
那比"按工作目录猜"可靠得多。

## 它不做什么

**不修改 `.env` 文件本身**，也不影响 `pydantic-settings` 的读取。
它只阻止"第三方库把配置偷偷塞进进程环境"。

## 用法

在**任何可能导入 pymilvus 的入口**最先导入本模块：

```python
from app.core import block_dotenv_pollution  # noqa: F401
```

已接入 `app/main.py`（唯一的生产入口）。
测试脚本若要导入 milvus 相关模块，也应先导入它。
"""

import os
import sys

from app.core.logging import get_logger

logger = get_logger("app.block_dotenv")

# 被替换掉的原始函数，供需要时恢复（例如排查问题）
_original_load_dotenv = None

# 这个模块被导入过吗 —— 避免重复打日志
_installed = False


def install() -> bool:
    """把 `dotenv.load_dotenv` 换成空操作。

    返回是否**首次**安装成功。

    可以安全地重复调用。
    """

    global _original_load_dotenv, _installed

    if _installed:
        return False

    try:
        import dotenv
    except ImportError:
        # 没装 python-dotenv：那 pymilvus 也不会注入，无事可做。
        _installed = True
        return False

    _original_load_dotenv = dotenv.load_dotenv

    def _noop(*args, **kwargs):
        """不做事，但**记一条日志**。

        为什么不完全静默：如果将来某个库**真的需要**读 `.env`
        才能工作，静默会让那个需求变成"莫名其妙地连不上"。
        记一条 debug 级别的日志，需要时能看到。
        """

        logger.debug(
            "已阻止一次 load_dotenv 调用（args=%s kwargs=%s）——"
            "这是刻意的，见 app/core/block_dotenv_pollution.py",
            args,
            kwargs,
        )

        return False

    dotenv.load_dotenv = _noop

    # `from dotenv import load_dotenv` 会拿到模块属性，
    # 因此上面的替换对那种写法也生效。
    #
    # 但若某个模块在**我们替换之前**已经执行过
    # `from dotenv import load_dotenv`，它持有的是旧引用 ——
    # 那种情况无法撤回，因此本模块必须在最前面导入。
    _installed = True

    return True


def uninstall() -> None:
    """恢复原始 `load_dotenv`。**只给测试用。**"""

    global _installed

    if _original_load_dotenv is None:
        return

    import dotenv

    dotenv.load_dotenv = _original_load_dotenv
    _installed = False


def pollution_snapshot() -> dict[str, str]:
    """返回当前 `os.environ` 中**来自 `.env` 的键**。

    用于验证"污染是否已阻止"。

    判据不是"环境里有没有这些键" —— 因为 CI 或部署环境
    可能**合法地**通过环境变量注入配置。这里只列出键名，
    供调用方与 `.env` 的内容对比。
    """

    # 只关心"敏感且来自 .env"的那几个
    watched = (
        "SECRET_KEY",
        "DATABASE_URL",
        "REDIS_URL",
        "LLM_API_KEY",
        "LLM_BASE_URL",
        "LLM_MODEL",
        "EMBEDDING_API_KEY",
        "EMBEDDING_BASE_URL",
        "EMBEDDING_MODEL",
    )

    return {
        key: os.environ[key]
        for key in watched
        if key in os.environ
    }


# 导入即生效 —— 调用方只需 `import` 它，不必记得调用 install()。
# 这减少了一次"忘记调用"的机会，而那种忘记会静默失效。
if "pytest" not in sys.modules:
    install()
