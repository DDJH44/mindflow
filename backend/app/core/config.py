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

    model_config = SettingsConfigDict(
        env_file=BASE_DIR / ".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )


settings = Settings()