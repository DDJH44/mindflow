# 部署到云服务器

把 MindFlow 放到公网上让别人体验。

> **先读这一节，它决定了你要做哪些额外步骤。**
>
> 这个项目会**真实花你的钱**：每一道面试题都要调用你的
> LLM 与嵌入 API。因此"公开部署"不只是技术问题——
> **谁能注册，就等于谁能花你的额度。**
>
> 本文默认按**邀请制**部署（只给你发码的人使用）。

---

## 0. 先想清楚：成本从哪里来

| 现状 | 后果 |
| --- | --- |
| 注册开放 | 任何陌生人都能给自己开一个带额度的账号 |
| 每个账号自带 10 场面试 + 100 题 | 每多一个账号，就多 10 场你付费的额度 |
| 每道题约 2–4 次 LLM 调用 + 一次嵌入调用 | 成本直接落在你的 key 上 |

限流（`RATE_LIMIT_*`）解决的是"**单个人用多少**"，
解决不了"**多少人可以用**"。后者由**邀请码**控制：

> **一次性邀请码 → 最多账号数 = 发出去的码数**

因此成本是**可以预先算出来的**：发 10 条码，最多少 100 场。

本项目已实现该机制（`REGISTRATION_REQUIRE_INVITE=true`）。

---

## 1. 服务器要求

| 项 | 最低 | 建议 | 为什么 |
| --- | --- | --- | --- |
| CPU | 2 核 | 2 核 | Milvus 与 Postgres 各自都要一个 |
| **内存** | **4 GB** | **8 GB** | **实测过 Milvus 被 OOM 杀掉**（`Exited (137)`）。而内存不足时后端"能启动、能读数据库，但检索一直失败" |
| 磁盘 | 20 GB | 40 GB | 镜像约 1 GB + 数据增长 |
| 系统 | Ubuntu 22.04+ | Ubuntu 24.04 | 任何装得动 Docker 的 Linux 都行 |

> ⚠️ **内存是最容易踩的坑。** 那 5 个容器理论上能挤进 2 GB，
> 但一次上传 + 嵌入就会触发 OOM，而 OOM 的表现是
> **容器静默重启**（`Exited (137)`），不报任何应用错误。
> 你会看到"索引一直不完成"，而日志里什么也没有。
>
> 加 swap 可以缓解，但不能替代内存。

安装 Docker：

```bash
curl -fsSL https://get.docker.com | sh
sudo usermod -aG docker $USER   # 重新登录后生效
```

---

## 2. 拿到代码

```bash
git clone <你的仓库地址> mindflow
cd mindflow
```

---

## 3. 配置密钥

```bash
cp .env.prod.example .env.prod
chmod 600 .env.prod
```

然后**逐项**填写。下面每一项都说明"不填会怎样"：

### 3.1 必须自己生成的三项

```bash
# ① 数据库密码
openssl rand -base64 24

# ② MinIO 凭据（Milvus 用它存索引文件）
openssl rand -base64 24    # access key
openssl rand -base64 32    # secret key

# ③ JWT 签名密钥
openssl rand -base64 48
```

填进 `.env.prod`：

```ini
POSTGRES_PASSWORD=<①的结果>
MINIO_ACCESS_KEY=<②的第一个>
MINIO_SECRET_KEY=<②的第二个>
SECRET_KEY=<③的结果>
```

> **为什么 `SECRET_KEY` 必须换**：占位密钥最危险的地方是它
> **能正常工作** —— 本地测试、部署全都不报错，
> 直到有人用它伪造出任意用户的登录令牌。
> 本项目已加守卫：`APP_ENV=production` 且密钥是占位值时
> **拒绝启动**。

### 3.2 模型端点

```ini
LLM_API_KEY=...
LLM_BASE_URL=...
LLM_MODEL=...

EMBEDDING_API_KEY=...
EMBEDDING_BASE_URL=...
EMBEDDING_MODEL=...
```

可以用与本地相同的值（`.env` 里那份）。

> ⚠️ **建议为线上单独申请一个 key**，而不是复用本地那个。
> 理由：线上 key 会暴露给一个被公网访问的服务；
> 出问题时也可以单独吊销，不影响你本地开发。

### 3.3 把额度调小

```ini
INTERVIEW_QUOTA=3
MONTHLY_QUESTION_QUOTA=30
```

> **这是最直接的降本手段。** 即使邀请码泄露，
> 损失上限也是 `码数 × 每月额度`。
> 体验期用 3 场 / 30 题足够。

### 3.4 确认注册控制

```ini
REGISTRATION_REQUIRE_INVITE=true
```

**这一项如果漏了，前面所有成本分析都不成立。**

---

## 4. 启动

> ⚠️ **必须带 `--env-file .env.prod`。**
>
> `docker-compose.prod.yml` 里的 `env_file:` 只把变量**注入容器**，
> **不提供 `${VAR}` 插值** —— compose 读的是项目根的 `.env`。
> 不加这个参数会报 `POSTGRES_PASSWORD 未设置`，
> 而错误信息看起来像"我没填"，其实你填了。
>
> 实测踩过这一个。

```bash
docker compose --env-file .env.prod -f docker-compose.prod.yml up -d --build
```

首次构建约 3–6 分钟（下载镜像 + 装依赖）。

看状态：

```bash
docker compose --env-file .env.prod -f docker-compose.prod.yml ps
```

应该看到 7 个 `Up`，其中 `mindflow-migrate` 是
`Exited (0)`（一次性迁移任务，正常）。

> ⚠️ **`mindflow-migrate` 必须是 `Exited (0)`。**
> 若它是 `Exited (1)`，说明迁移失败，而**后端不会启动**
> （`depends_on: service_completed_successfully`）。
> 查它的日志：`docker logs mindflow-migrate`

---

## 5. 验证

```bash
# 后端健康
curl -s http://localhost/api/health
# 期望：{"status":"ok","service":"MindFlow AI","environment":"production"}

# 注册模式（应是 true）
curl -s http://localhost/api/auth/registration-mode
# 期望：{"require_invite":true}
```

浏览器打开 `http://<服务器IP>/`，应看到深色的登录页。

### 5.1 确认端口没有暴露

```bash
sudo ss -tlnp | grep -E '5432|6379|19530|9000'
```

**应该没有任何输出。**

> 若这里列出了端口，说明数据库/向量库对整个公网开放 ——
> 任何人扫到就能连。这是云上最常见的事故，
> 而且它**不会报错**，只会在某天你发现数据没了。
>
> 本项目的 `docker-compose.prod.yml` 已经**不发布**这些端口。
> 这一条是给你自己在改了文件之后复核用的。

---

## 6. 发邀请码

```bash
docker compose --env-file .env.prod -f docker-compose.prod.yml exec backend \
  uv run python -m scripts.manage_invites create \
  --count 5 --note "内测朋友" --expires-days 14
```

输出：

```
=====================================================
已签发 5 条邀请码
=====================================================
  备注：内测朋友
  过期：2026-11-01 08:00（UTC）

    EZAB-SXFS-M7DT-QFPM
    DBBX-JR4X-D94U-FMJE
    ...
```

**每条只能用一次。** 把码发给对应的人，对方在注册页填入即可
（大小写、连字符、空格都不影响）。

管理：

```bash
# 看还有多少可用
... exec backend uv run python -m scripts.manage_invites list --unused

# 撤销一条没用过的（已用过的不能删 —— 它是注册记录）
... exec backend uv run python -m scripts.manage_invites revoke <码>
```

---

## 7. HTTPS（**不要跳过**）

现在登录密码是**明文**在网络上传输的。必须上 TLS。

### 做法 A：域名 + Let's Encrypt（推荐）

1. 域名 A 记录指向服务器 IP
2. 把 `.env.prod` 的 `HTTP_PORT` 改成 `127.0.0.1:80`
   （让 nginx 容器只听本机，由宿主机的 TLS 终止处理公网）
3. 宿主机装 nginx + certbot：

```bash
sudo apt install -y nginx certbot python3-certbot-nginx
```

`/etc/nginx/sites-available/mindflow`：

```nginx
server {
    listen 80;
    server_name mindflow.example.com;

    location / {
        proxy_pass http://127.0.0.1:80;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        # ⚠️ 必须覆写而不是追加，否则客户端可以伪造 IP 绕过限流
        proxy_set_header X-Forwarded-For $remote_addr;
        proxy_set_header X-Forwarded-Proto $scheme;

        # 与容器内的超时保持一致（生成题目可能要 60 秒）
        proxy_read_timeout 300s;
        proxy_send_timeout 300s;
        # 大于后端的 5 MB 上传上限
        client_max_body_size 8m;
    }
}
```

```bash
sudo ln -s /etc/nginx/sites-available/mindflow /etc/nginx/sites-enabled/
sudo nginx -t && sudo systemctl reload nginx
sudo certbot --nginx -d mindflow.example.com
```

certbot 会自动改配置并申请证书。

### 做法 B：没有域名

用 `https://<IP>.nip.io` 这类服务，或直接用 IP + 自签证书
（浏览器会警告，不适合给别人用）。

> **没有 HTTPS 时不要对外开放。** 登录密码会被同一网络上的
> 任何人看到。

---

## 8. 防火墙

```bash
sudo ufw allow 22/tcp
sudo ufw allow 80/tcp
sudo ufw allow 443/tcp
sudo ufw enable
```

> ⚠️ 只开这三个。**不要**开 5432 / 6379 / 19530 / 9000。
> 就算 docker 的端口没有发布到宿主机，多一层防火墙也更稳。

---

## 9. 日常运维

```bash
# 看日志
docker compose --env-file .env.prod -f docker-compose.prod.yml logs -f backend

# 重启后端（改完代码后）
docker compose --env-file .env.prod -f docker-compose.prod.yml up -d --build backend

# 备份数据库（**定期做**）
docker exec mindflow-postgres pg_dump -U mindflow mindflow \
  | gzip > backup-$(date +%F).sql.gz

# 清理孤儿上传文件（文件存在但数据库里没有记录）
docker compose --env-file .env.prod -f docker-compose.prod.yml exec backend \
  uv run python -m scripts.cleanup_orphan_uploads

# 检查向量与数据库是否一致
docker compose --env-file .env.prod -f docker-compose.prod.yml exec backend \
  uv run python -m scripts.check_embedded_consistency
```

---

## 10. 已知限制（部署前应该知道）

| 限制 | 说明 |
| --- | --- |
| **索引 worker 跑在应用进程内** | 单实例没问题。要多副本时把 `WORKER_ENABLED` 设为 `false`，并另起一个 worker 容器（`FOR UPDATE SKIP LOCKED` 保证不会重复处理） |
| **没有自动备份** | 只有上面那条手工 `pg_dump`。**上线后第一件事就是配定时备份** |
| **没有监控告警** | 服务挂了不会通知你。最低限度：`docker compose ps` 里出现 `Restarting` 就该查 |
| **单机部署，没有高可用** | 服务器宕机 = 服务中断。对"给别人体验"够用 |
| **没有管理员后台** | 邀请码管理走 CLI。这是刻意的：后台意味着新的攻击面，而发码是低频操作 |
| **Milvus 内存需求较高** | 见 §1。不可用时后端仍能启动，但**所有资料检索会失败**（面试退化成通用题） |

---

## 11. 出问题时怎么查

从外到内，逐层确认：

```bash
# ① 容器都起来了吗
docker compose --env-file .env.prod -f docker-compose.prod.yml ps

# ② 迁移成功了吗（必须是 Exited 0）
docker inspect mindflow-migrate --format '{{.State.ExitCode}}'

# ③ 后端活着吗
curl -s http://localhost/api/health

# ④ 后端连得上数据库吗
docker compose --env-file .env.prod -f docker-compose.prod.yml exec backend \
  uv run python -m scripts.doctor

# ⑤ 前端能到达后端吗
curl -s http://localhost/api/auth/registration-mode

# ⑥ 有容器被 OOM 杀过吗（137 = 内存不足）
docker ps -a --format '{{.Names}}\t{{.Status}}' | grep -i exited
```

常见现象与真因：

| 现象 | 真因 |
| --- | --- |
| 页面能开，登录报"网络错误" | 后端没起，或 `/api` 代理没通。查 ③⑤ |
| 上传文件失败，后端日志无记录 | **nginx 的 `client_max_body_size`**（默认 1 MB）。本项目已设为 8 MB |
| 开始面试 60 秒后报 504 | **nginx 的 `proxy_read_timeout`**（默认 60 秒）。本项目已设为 300 秒 |
| 所有人都被限流 | `X-Forwarded-For` 可能是**追加**而非覆写，导致真取到代理 IP。见 §7 配置里的注释 |
| 资料显示"已索引"但面试只出通用题 | Milvus 不可用（OOM 或没起）。查 ⑥ 与 Milvus 日志 |
| 重启后资料全丢 | `uploads_data` 卷没挂上 |
| 启动即失败，日志说 SECRET_KEY | 用了占位密钥，而 `APP_ENV=production`。见 §3.1 |
