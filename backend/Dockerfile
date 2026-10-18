# 后端镜像。
#
# ## 为什么用 uv 而不是 pip
#
# 项目已经有 `uv.lock`（430 KB），它锁定了每一个包的**精确版本**。
# 用 `pip install -r` 需要另生成一份 requirements，而那份会和
# lock 文件漂移 —— 最后出现"本地能跑、线上装出来的版本不同"。
#
# `uv sync --frozen` 直接按 lock 安装，**不允许**顺手升级：
# 构建可复现，且不会因为某天上游发新版而突然坏掉。
#
# ## 为什么先复制 pyproject/uv.lock 再复制源码
#
# Docker 的层缓存按"内容是否变化"失效。依赖安装是最慢的一步，
# 而改一行 Python 代码不需要重装依赖。分成两层后，
# 日常改代码的构建从数分钟降到数秒。

FROM python:3.13-slim AS base

# 时区与 locale：容器默认 UTC。
#
# 应用内部统一用 UTC 存时间（见 app/core/time_convention.py），
# 因此**不需要**改时区 —— 保持 UTC 反而避免了"数据库时区不是 UTC"
# 那类混存问题（time_convention 的测试会断言服务器时区是 UTC）。
ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1

# uv：从官方镜像取二进制，不通过 pip 安装（更快、更小）。
COPY --from=ghcr.io/astral-sh/uv:0.5.11 /uv /usr/local/bin/uv

WORKDIR /app

# ---- 依赖层 ----
COPY pyproject.toml uv.lock ./

# `--no-dev`：不装 pytest 等开发依赖。
# `--frozen`：严格按 lock 安装，不做任何解析升级。
RUN uv sync --frozen --no-dev --no-install-project

# ---- 源码层 ----
COPY alembic.ini ./
COPY alembic ./alembic
COPY app ./app

# 上传目录。
#
# ⚠️ **必须挂卷**（见 docker-compose.prod.yml）。
# `app/routers/documents.py` 把文件写到相对路径 `uploads/documents`，
# 相对的是**工作目录**（此处 /app）。
# 不挂卷时，容器一重建，磁盘上的文件就没了 ——
# 而数据库里的记录还在，于是每份资料都指向一个不存在的文件。
# 表现为"资料显示已索引，但面试出题检索不到"。
RUN mkdir -p /app/uploads/documents

ENV PATH="/app/.venv/bin:$PATH"

# 非 root 运行。
#
# 为什么重要：容器逃逸类漏洞的第一步通常就是拿到容器内的 root。
# 而应用本身不需要写系统目录 —— 只写 /app/uploads。
RUN useradd --create-home --uid 10001 appuser \
    && chown -R appuser:appuser /app

USER appuser

EXPOSE 8000

# 健康检查：让 compose / 编排器能判断"真的在服务"，
# 而不只是"进程还在"。
#
# 用 python 而不是 curl：slim 镜像里没有 curl，
# 为健康检查装一个 HTTP 客户端不值得。
HEALTHCHECK --interval=30s --timeout=5s --start-period=40s --retries=3 \
    CMD python -c "import urllib.request,sys; \
sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8000/api/health', timeout=4).status == 200 else 1)"

# `--host 0.0.0.0`：容器内必须监听所有网卡，
# 否则宿主机/nginx 连不上（默认的 127.0.0.1 只允许容器内访问）。
#
# `--proxy-headers` + `--forwarded-allow-ips`：
# 让 uvicorn 采信 nginx 传来的 X-Forwarded-*，
# 这样限流拿到的是**真实客户端 IP** 而不是 nginx 的 IP。
# 不设的话所有用户共用 nginx 一个 IP，限流会误伤全部人。
CMD ["uvicorn", "app.main:app", \
     "--host", "0.0.0.0", \
     "--port", "8000", \
     "--proxy-headers", \
     "--forwarded-allow-ips", "*", \
     "--no-access-log"]
