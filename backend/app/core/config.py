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
    # 签名密钥。**必须是随机值，且不能提交到仓库。**
    #
    # ⚠️ 实测坑：`.env` 里长期留着
    # `mindflow_2026_development_secret_key_change_later` ——
    # 一个**看起来像配置、实际是占位符**的值。
    # 这种值最危险的地方是它**能正常工作**：
    # 本地开发、测试、甚至部署都不会报错，
    # 直到有人用它伪造出合法的登录令牌。
    #
    # 生成方式（见 `app/core/generate_secret.py`）：
    #     uv run python -m app.core.generate_secret
    #
    # 启动时会校验它（见 `main.py` 的 `_check_secret_key`）：
    # **非 development 环境下用占位值会拒绝启动**。
    secret_key: str

    algorithm: str = "HS256"

    # =====================
    # CORS
    # =====================
    # 允许的跨域来源，逗号分隔。**默认空 = 不启用 CORS**。
    #
    # 为什么默认空是**正确**的：前端把 `/api` 代理到后端
    # （见 `web/vite.config.ts`），因此浏览器看到的是**同源**请求，
    # 根本不需要 CORS。
    #
    # 而"为了省事加一个 `allow_origins=["*"]`"是个常见错误：
    # 那会让**任意网站**都能带着用户的凭据调这个 API。
    #
    # 只有前端与后端**不同源**部署时才需要配置，例如：
    #     CORS_ALLOW_ORIGINS=https://mindflow.example.com
    cors_allow_origins: str = ""

    # =====================
    # 注册控制
    # =====================
    # 是否要求邀请码才能注册。
    #
    # ⚠️ **公开部署时必须为 True。**
    #
    # 为什么这不是"可选的加固"而是"必须"：注册开放意味着
    # 任何陌生人都能给自己开一个带「10 场面试 + 100 题」额度的
    # 账号，而每一道题都会真实调用**部署者的** LLM 与嵌入 API ——
    # 也就是真实花钱。
    #
    # 限流（`rate_limit_*`）解决的是"**单个人用多少**"，
    # 而这一项解决的是"**多少人可以用**"。两件事，
    # 只做前者挡不住成本。
    #
    # 默认 False 是为了本地开发与测试方便（既有套件会注册账号）；
    # 生产部署必须显式设 True。
    registration_require_invite: bool = False

    # =====================
    # 嵌入配置
    # =====================

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
    # 检索重排（已实测否定，保留代码供复查）
    # =====================
    # ⚠️ **保持 False，除非你愿意先重跑全部三项测量。**
    #
    # 这是本仓库里唯一一处"实现了但不该启用"的功能，
    # 因此把否定它的证据完整留在这里，避免将来有人
    # 重新"发现"它并启用。
    #
    # 测量结论（2026-10-12，`tests/test_rerank*.py`）：
    #
    # 1. **产品指标上是净负收益。** 面试每类型取 3 块喂给模型，
    #    因此产品关心的是"答案有没有进那 3 块"，而不是排第几。
    #    按这个口径（`test_rerank_context.py`）：
    #
    #        现状：直接取 3           22/23 = 0.957
    #        取 10 → 重排 → 留 3      21/23 = 0.913   ← 更差
    #
    #    救回 0 例、弄丢 1 例。
    #
    # 2. **它优化的 R@1 是产品不用的指标。** 重排确实把
    #    chunk 级 R@1 从 0.652 提到 0.739 —— 但那只是把
    #    已经在上下文里的块换了顺序，模型本来就都看得到。
    #
    # 3. **代价是单次检索中位 19.6 秒**（含 embedding），
    #    每次出题调 4 个类型，串行即 78 秒。
    #    日志里大量 `APITimeoutError` 与"重排结果无法解析"。
    #
    # **更好的做法**（已采纳）：每类型多取两块。
    # `InterviewContextBuilder.DEFAULT_LIMIT_PER_TYPE` 从 3 提到 5，
    # 产品指标即达 **1.000**，零模型调用、零额外延迟、
    # 且不引入重排那三种新的失败模式。
    rerank_enabled: bool = False

    # 重排时的候选池大小。同样只在 `rerank_enabled` 为真时生效。
    rerank_candidates: int = 10

    # =====================
    # 上传体积上限
    # =====================
    # ## 为什么必须有这两条
    #
    # 此前上传**只校验扩展名，不校验体积**。后果串起来是：
    #
    #     整个文件 → 切出成千上万块 → 全部送进嵌入 API
    #
    # 而嵌入 API 是**按量计费**的，注册又是开放的、无限流 ——
    # 也就是说没有东西挡着别人替你花掉配额。
    #
    # 这不是理论风险：D59 的触发场景就是"超过约 1 万字符的文件"，
    # 说明大文件路径此前从未被正常走过。
    #
    # ## 为什么是两个维度，而不是一个
    #
    # 它们挡的是**两种不同的成本**：
    #
    # | 维度 | 挡什么 |
    # | --- | --- |
    # | 字节数 | 内存与磁盘（上传是流式写盘，峰值只占 1MB 缓冲）|
    # | **块数** | **嵌入 API 调用次数 —— 真正花钱的那一条** |
    #
    # 只看字节数不够：一个 800KB 的文本文件能切出上千块；
    # 而一个 5MB 的扫描版 PDF 可能一个字都抽不出来（零成本）。
    # 反过来只看块数也不够：字节数决定磁盘占用。
    #
    # ## 数值的依据（见 `app/core/measure_document_sizes.py`）
    #
    # 实测：评估语料约 2700 字符 / 6 块；真实简历 1752 字符 / 5 块；
    # 而测试残留的大文件达 844KB、曾切出上千块。
    #
    # - 字节 5MB：给"几十页的 PDF 项目文档"留足余量
    #   （真实简历只有 1.7KB）
    # - 字符 25 万：与块数上限呼应（500 字符/块 × 500 块）
    # - 块数 500：约等于 25 万字符，是正常材料的两个数量级以上，
    #   同时把单次上传的嵌入调用压在可控范围内
    max_upload_bytes: int = 5 * 1024 * 1024

    max_document_chars: int = 250_000

    max_chunks_per_document: int = 500

    # =====================
    # 限流（§32）
    # =====================
    # D67 修的是"**一个**请求能花很多钱"，而限流挡的是
    # "**很多次**请求" —— 两者是配套的：注册开放且此前无限流，
    # 意味着可以反复触发昂贵的上传与嵌入。
    #
    # 数值都很宽松：目的是**防滥用**，不是给正常用户添麻烦。
    # 一个真实用户一天上传十几份材料、注册一次 —— 碰不到上限。
    rate_limit_enabled: bool = True

    # 注册：按 IP。这是最需要限的一条 ——
    # 无限注册是"反复触发昂贵操作"的起点。
    rate_limit_register_times: int = 5
    rate_limit_register_seconds: int = 3600

    # 登录：按 IP。防暴力破解口令。
    rate_limit_login_times: int = 20
    rate_limit_login_seconds: int = 3600

    # 上传：按用户。这是**直接花钱**的那条（切块 → 嵌入调用）。
    rate_limit_upload_times: int = 30
    rate_limit_upload_seconds: int = 3600

    # 开始面试：按用户。每场面试会多次调用 LLM 与嵌入。
    rate_limit_start_interview_times: int = 10
    rate_limit_start_interview_seconds: int = 3600

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