from app.core.security import (
    hash_password,
    verify_password,
)


password = "123456"

hashed = hash_password(password)

print("原始密码：", password)

print("加密密码：", hashed)

print(
    "验证结果：",
    verify_password(
        password,
        hashed,
    )
)