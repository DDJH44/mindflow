"""生成一个可用于生产的 `SECRET_KEY`。

## 为什么要有一个专门的脚本

"随便敲一串"和"生成一个密码学安全的随机值"是两件事，
而人很难凭感觉区分。实测这个项目的 `.env` 里长期躺着：

    mindflow_2026_development_secret_key_change_later

它**看起来像配置**（有年份、有项目名、格式正常），
而实际是占位符 —— 这种值最危险的地方是它**能正常工作**：
开发、测试、部署全都不报错，直到有人用它伪造出合法的登录令牌。

因此把"生成"变成一条命令，而不是一个靠自觉的动作。

## 用法

    uv run python -m app.core.generate_secret

把输出写进 `.env` 的 `SECRET_KEY=`。

⚠️ **换密钥会让所有已签发的令牌立即失效** ——
所有用户需要重新登录。这是预期行为（也是一种应急手段：
怀疑令牌泄露时就该换）。
"""

import secrets
import sys


def generate() -> str:
    """生成 64 字节的 URL-safe 随机串（约 86 字符）。

    为什么是 64 字节：HS256 的安全性取决于密钥的**熵**。
    32 字节（256 位）已是密码学下限，取 64 字节是留余量 ——
    密钥长了不影响性能（HMAC 对超长密钥会先哈希）。
    """

    return secrets.token_urlsafe(64)


def main() -> int:
    value = generate()

    print()
    print("=" * 70)
    print("新的 SECRET_KEY")
    print("=" * 70)
    print()
    print(f"  {value}")
    print()
    print("  把它写进 .env：")
    print()
    print(f"    SECRET_KEY={value}")
    print()
    print("  ⚠️ 换密钥会让**所有已签发的令牌立即失效**，")
    print("     所有用户需要重新登录。")
    print()

    return 0


if __name__ == "__main__":
    sys.exit(main())
