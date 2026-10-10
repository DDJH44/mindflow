from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict


# MindFlow 项目根目录
BASE_DIR = Path(__file__).resolve().parents[3]


class Settings(BaseSettings):

    # =====================
    # 应用配置
    # =====================
    app_name: str = "MindFlow AI"
    app_env: str = "development"

    # =====================
    # 数据库配置
    # =====================
    database_url: str
    redis_url: str

    # =====================
    # 大模型配置
    # =====================
    llm_api_key: str = ""
    llm_base_url: str = ""
    llm_model: str = ""

    # =====================
    # JWT 配置
    # =====================
    secret_key: str

    algorithm: str = "HS256"

    embedding_api_key: str = ""
    embedding_base_url: str = ""
    embedding_model: str = ""

    access_token_expire_minutes: int = 1440

    # =====================
    # 评估采样配置
    # =====================
    # 线上路径与离线评估对"评分可复现性"的要求不同：
    #
    # - 离线评估需要可复现，因此多次采样后按多数档位合并
    #   （见 app/services/interview/answer_analyzer.py）。
    # - 线上答题时用户要等结果，每次额外采样都是成倍延迟，
    #   因此线上只采 1 次。
    #
    # ⚠️ 这意味着**线上分数比评估分数波动更大**。
    # 两者不可直接比较，详见 MIND_FLOW_PLAN.md §8A.10。
    online_analysis_samples: int = 1
    online_evaluation_samples: int = 1

    # 离线评估使用的采样次数。必须是奇数，
    # 否则平票时多数票退化为"偏向第一次采样"。
    offline_analysis_samples: int = 3
    offline_evaluation_samples: int = 3

    # =====================
    # 日志配置
    # =====================
    # 是否打印每条 SQL。
    #
    # 默认关闭。开启时 SQLAlchemy 会**用自己的 handler** 再打一遍，
    # 于是每条 SQL 出现两次、应用的访问日志被淹没。
    #
    # ⚠️ 此前这是 `create_async_engine(..., echo=True)` **硬编码**，
    # `.env` 里关不掉 —— 属于"调试开关被写死进代码"。
    # 需要看 SQL 时在 `.env` 设 `SQL_ECHO=true`。
    sql_echo: bool = False

    # =====================
    # 嵌入配置
    # =====================
    # 单次嵌入请求的最大文本条数（初始值）。
    #
    # ⚠️ 这个上限**不是可选的**：当前端点会直接拒绝过大的批量
    # （`batch size is invalid, it should not be larger than 10`）。
    # 此前代码把整个文档的 chunk 一次性发出，于是
    # **超过约 1 万字符的文件必然上传失败**（25 块 > 上限）。
    #
    # 实测该端点的真实上限是 **10**（11 条即被拒）。
    # 但**不要把它当成事实**：同一端点在另一次测量里声称 20，
    # 换端点（OpenAI 自身是 2048）差异更大。因此
    # `OpenAIEmbeddingService` 还会从端点的报错里**自适应学习**
    # 真实上限并缓存 —— 这里的值只是初始猜测。
    embedding_batch_size: int = 10

    # =====================
    # 异步索引 worker
    # =====================
    # 是否在应用进程内跑索引 worker（§28）。
    #
    # 默认开启：这样 `uv run uvicorn` 一条命令就能用，
    # 本地开发不需要额外起进程。
    #
    # 部署多副本时必须设为 false 并在别处起独立 worker ——
    # 否则每个副本都会跑一个 worker。**功能上不会出错**
    # （`FOR UPDATE SKIP LOCKED` 保证同一任务只被领一次），
    # 但那是在浪费连接与轮询。
    worker_enabled: bool = True

    # 空队列时的轮询间隔（秒）。
    #
    # 用 sleep 而不是长轮询/通知机制：实现简单，代价是空队列时
    # 每 N 秒一次索引查询。那个代价远小于引入新基础设施
    # （例如 Redis 队列）来消除它。
    worker_poll_seconds: float = 2.0

    model_config = SettingsConfigDict(
        env_file=BASE_DIR / ".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )


settings = Settings()