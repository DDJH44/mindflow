"""向量库（Milvus）的可用性判断。

## 为什么需要它（2026-10-20 实测事故）

Milvus 容器没在运行时，`POST /interviews/{id}/start` 在生成首题的
检索阶段抛出 `grpc._channel._InactiveRpcError`（连接 :19530 被拒），
它**不命中任何既有 except 分支**，于是变成裸 500
"Internal Server Error"。

用户端的表现是"点击开始面试没反应"（前端不跳转），而真实原因
（依赖不可用，不是代码缺陷）被完全掩盖 —— 这与 D57 修 LLM 裸 500
时的判断标准一致：**依赖不可用必须与代码错误可区分**。

## 判据为什么用类名与 MRO（而不是 import pymilvus）

与 `is_llm_error` 同一理由：本项目允许替换向量库实现
（Milvus v1 → v2 已迁移过一次），硬绑 SDK 异常类会让判断在
替换后失效。grpc 的 `_InactiveRpcError` 是 pymilvus 底层真实
冒上来的类型（前面的事故日志即为它），必须识别。
"""

# 向量库异常的类名特征。
#
# - `MilvusException` / `MilvusUnavailableException`：pymilvus 自己的
#   异常层级（服务端拒绝、不可用）。
# - `_InactiveRpcError` / `RpcError`：gRPC 连接层（容器没起、端口
#   不通）。前缀下划线说明它是私有类，但 pymilvus 不总是包装它 ——
#   实测它会直接冒到路由层。
VECTOR_STORE_ERROR_NAMES = frozenset(
    {
        "MilvusException",
        "MilvusUnavailableException",
        "_InactiveRpcError",
        "RpcError",
    }
)


def is_vector_store_error(exc: BaseException) -> bool:
    """判断异常是否表示"向量库不可用"。

    判据与 `is_llm_error` 相同（同一事实只应存在一种写法）：

    1. 自身 MRO 里的类名命中特征集
    2. `__cause__` / `__context__` 链 —— 服务层若把连接错误包进
       自定义异常（例如检索服务的 RuntimeError），类型信息只
       存在于链上。
    """

    if {cls.__name__ for cls in type(exc).__mro__} & (
        VECTOR_STORE_ERROR_NAMES
    ):
        return True

    for chained in (exc.__cause__, exc.__context__):
        if chained is None:
            continue

        if {
            cls.__name__ for cls in type(chained).__mro__
        } & VECTOR_STORE_ERROR_NAMES:
            return True

    return False
