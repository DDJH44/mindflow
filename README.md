# MindFlow

面向求职者的 AI 模拟面试助手。基于**你自己的简历、岗位 JD、项目材料与代码**，
通过 RAG 检索生成可追溯、可追问、可评估的个性化模拟面试。

所有提问都能指回具体资料片段；资料在项目与用户之间严格隔离。

---

## 技术栈

| 层 | 选型 |
| --- | --- |
| 后端 | FastAPI + async SQLAlchemy |
| 数据库 | PostgreSQL 16 |
| 向量库 | Milvus 2.5（1024 维，COSINE）|
| 缓存 | Redis 7（**当前未接入业务代码**）|
| 对象存储 | MinIO（Milvus 的依赖）|
| 前端 | Vue 3 + TypeScript + Vite（**无 UI 组件库**，手写 CSS）|
| 模型 | LLM 与 Embedding 均走 OpenAI 兼容端点 |

---

## 一、启动

### 推荐：一键脚本

```powershell
powershell -File scripts\start-all.ps1
```

它会依次：启动 Docker Desktop（如未运行）→ 起 5 个容器 →
起后端 → 起前端 → **验证两个 health 端点**。
冷启动实测约 **36 秒**。

服务以**独立进程**运行（脱离当前终端），日志写到 `.logs\`：

```powershell
powershell -File scripts\stop-all.ps1              # 只停后端与前端
powershell -File scripts\stop-all.ps1 -Containers  # 连容器一起停
```

> ⚠️ 用 `powershell` 而不是 `pwsh`：本机是 **Windows PowerShell 5.1**，
> 没有安装 PowerShell 7。脚本对两者都兼容，但**文件必须以
> UTF-8 with BOM 保存** —— 见下方"已知的坑"。

---

### 手动启动（需要 3 个终端）

改动脚本或排查问题时用这个。

### 1. Docker 容器

**先确认 Docker 引擎在跑。** 引擎没起时，
`docker ps -a` 会返回**空**（而不是列出已停止的容器），
很容易被误读成"容器被删了"：

```powershell
docker info --format "{{.ServerVersion}}"
```

报 `failed to connect to the docker API at npipe://...` 就是引擎没起 ——
启动 **Docker Desktop** 并等它就绪（本机实测约 15 秒），
再执行下面的步骤。

---

有 5 个容器，但 **只有 `postgres` 与 `redis` 配了自动重启** ——
`etcd` / `minio` / `milvus` 在 compose 文件里没有 `restart` 策略，
因此**机器重启后必须手动起**。

**确认状态：**

```powershell
docker ps -a --format "{{.Names}}|{{.Status}}" | Select-String mindflow
```

**若不是全部 Up，启动缺的那些：**

```powershell
# 方式 A：直接起（最快）
docker start mindflow-etcd mindflow-minio mindflow-milvus

# 方式 B：用 compose（会自动处理依赖顺序）
cd D:\yuxi\MindFlow
docker compose up -d
```

> Milvus 依赖 etcd 与 minio，先起那两个更稳。
> 起来后再等约 15 秒，Milvus 需要一点时间才接受连接。
>
> `docker compose up -d` 会把进度输出到 **stderr**，
> 因此 PowerShell 里可能显示成红色错误 ——
> 只要最后 `docker ps` 里 5 个都是 Up，就是成功的。

### 2. 后端

```powershell
cd D:\yuxi\MindFlow\backend
uv run uvicorn app.main:app --host 127.0.0.1 --port 8000 --no-access-log
```
**两个参数不要改：**

- `--no-access-log` —— 应用自己已经记录访问日志（带 `request_id`）。
  不关掉 uvicorn 的，**每条请求会打印两行**。
- **不要加 `--reload`** —— 应用进程内跑着索引 worker，
  reload 会起两个。`FOR UPDATE SKIP LOCKED` 保证任务不会被重复处理，
  但两个 worker 空转是浪费。

`uv` 会自动使用 `.venv`，按 `pyproject.toml` 需要 **Python ≥ 3.13**。

### 3. 前端

```powershell
cd D:\yuxi\MindFlow\web
npx vite --port 5173
```

`node_modules` 已维护过，通常不需要 `npm install`；
若报缺依赖就补一次。

### 4. 打开

**http://127.0.0.1:5173**

> 请用它访问，**不要直连 8000**。
> 前端把 `/api` 代理到后端（见 `web/vite.config.ts`），
> 直连 8000 会缺少代理层的同域语义。

---

## 二、确认一切正常

```powershell
# 后端直连
curl.exe http://127.0.0.1:8000/api/health

# 前端代理（这条同时验证了前端到后端的代理链路）
curl.exe http://127.0.0.1:5173/api/health
```

两条都返回 `{"status":"ok",...}` 才算通。

**更完整的一步**（逐项检查依赖，并明确区分环境问题与代码问题）：

```powershell
cd D:\yuxi\MindFlow\backend
uv run python -m app.core.doctor --llm
```

它会报告 PostgreSQL / 数据库迁移版本 / Milvus 一致性级别 /
向量一致性 / LLM 端点 / Embedding 端点（含维度）。
**在排除它报出的问题之前，测试失败不能作为代码缺陷的证据。**

---

## 三、跑测试

```powershell
cd D:\yuxi\MindFlow\backend

# 快速回归：跳过所有需要外部模型的套件（约 40 秒）
uv run python -m app.core.test_all --offline

# 全量回归（需要 LLM 与 Embedding 都可用）
uv run python -m app.core.test_all
```

门禁会**先检查环境**，并且对三类问题分别处理：

| 情况 | 处理 |
| --- | --- |
| 后端 / 前端没起 | 拦停，并提示启动命令 |
| 数据库 / Milvus 连不上 | 拦停 |
| **嵌入端点不可用** | **跳过**依赖它的套件并说明原因 |
| 孤儿向量（数据问题） | 只提示，不拦停 |

**为什么"嵌入不可用"是跳过而不是失败**：那些套件测的是额度逻辑
与上传链路，它们没有坏，是前提缺失。报成失败会让门禁持续误报
直到端点恢复 —— 而人一旦习惯忽略门禁，它就不再有任何作用。

---

## 四、其他工具

```powershell
cd D:\yuxi\MindFlow\backend

# 界面走查：真实浏览器跑一遍七个页面，截图到 docs/ui-shots/
# 含 D58 / D55 / D56 的交互回归
uv run python -m app.core.ui_walkthrough

# 检索评估：自带语料，跑完输出 Recall@1/3/5 与 MRR
uv run python -m app.evaluation.runner

# 知识图谱：把材料抽成实体与关系（按需，不会自动跑）
uv run python -m app.core.extract_knowledge --project <id> --dry-run
```

---

## 五、模型端点

配置在根目录 `.env`（**不入库**，模板见 `.env.example`）。

| 变量 | 说明 |
| --- | --- |
| `LLM_API_KEY` / `LLM_BASE_URL` / `LLM_MODEL` | 面试提问与评价 |
| `EMBEDDING_API_KEY` / `EMBEDDING_BASE_URL` / `EMBEDDING_MODEL` | 检索用的向量 |

**换 LLM** 的流程见 `MIND_FLOW_PLAN.md` §26。

**换 Embedding 必须走 §29 的流程** —— 它比换 LLM 危险得多：

> 不同模型的**向量空间不兼容**。即使两者都是 1024 维，
> 用新模型编码 query 去和旧模型编码的资料比余弦距离，
> 得到的相似度**没有意义**（不是变差，是无意义）。
> 而且失效是**静默的**：检索照常返回结果、不报错，
> 只是那些结果与问题无关。

### 常见故障：额度问题

报错形如：

```text
400 AllocationQuota.FreeTierOnly
Free quota exhausted. To continue accessing the model on a paid basis,
please add funds or disable the "use free tier only" mode
```

**两个不同的原因，别混淆：**

1. 余额真的不足 → 充值
2. **余额够，但控制台里"仅使用免费额度"开关还开着** →
   关掉它即可，**不需要换模型、不需要重新嵌入**

第 2 种容易被误判成第 1 种。错误信息里的
`disable the "use free tier only" mode` 就是提示。

**额度耗尽时系统会怎样**（实测）：

| 能力 | 状态 |
| --- | --- |
| 登录 / 项目 / 资料增删查 / 历史 / 能力画像 / 已有报告 | ✅ 可用（纯读库）|
| **开始面试** | ❌ 检索要先嵌入 query |
| 上传资料的索引 | ❌ 切块成功，索引失败 |

错误提示是干净的 503（不是裸 500），且**失败的"开始面试"
不会扣场次额度**。

---

## 六、目录结构

```text
MindFlow/
├── MIND_FLOW_PLAN.md      项目总指挥文档（Source of Truth，含 ADR 与缺陷记录）
├── docs/
│   ├── UI_DESIGN.md       前端设计语言与体验规范
│   ├── ENGINEERING_ISSUES.md
│   └── ui-shots/          界面走查截图（机器可读结果见 walkthrough.json）
├── docker-compose.yml
├── backend/
│   ├── alembic/           数据库迁移
│   ├── evaluation_reports/ RAG 与面试评估报告（版本化，见计划书 §15）
│   ├── app/
│   │   ├── core/          配置、日志、中间件、验证与运维脚本
│   │   ├── models/        数据库模型
│   │   ├── repositories/  数据访问
│   │   ├── routers/       HTTP 端点
│   │   ├── schemas/       请求 / 响应契约
│   │   ├── services/      业务逻辑（含 interview/ 与 knowledge/）
│   │   └── evaluation/    检索与面试的离线评估
│   └── pyproject.toml
└── web/
    └── src/
        ├── api/           HTTP 客户端与类型
        ├── components/    可复用组件
        ├── pages/         七个页面
        ├── stores/        Pinia
        └── styles/app.css 设计令牌与全局样式
```

---

## 七、数据库迁移

```powershell
cd D:\yuxi\MindFlow\backend

uv run alembic current          # 当前版本
uv run alembic upgrade head     # 升到最新
uv run alembic downgrade -1     # 回退一步
```

> `alembic` 属于**开发依赖**（`dependency-groups.dev`）。
> 此前它没有声明，导致 `uv run alembic` 实际走的是全局 Python 里
> 装的版本 —— 换一台机器就跑不了迁移，而报错是
> `No module named 'alembic.config'`（因为 `import alembic` 会命中
> 项目根目录下的 `alembic/` 包，把"库整个缺失"伪装成
> "模块缺了个子模块"）。

---

## 八、已知的坑

### 8.1 `.ps1` 脚本必须是 UTF-8 **with BOM**

本机是 **Windows PowerShell 5.1**（没有安装 PowerShell 7 的 `pwsh`）。
5.1 读 `.ps1` 时，若文件**没有 BOM**，会按**本地代码页**解析 ——
于是中文被截断成乱码：

```text
"  完成。"    →   瀹屾垚銆?      ← 解析失败
```

报错是 `字符串缺少终止符` 与 `意外的标记 }`：

> ⚠️ **看起来像括号不配对，实际是编码问题。**
> 这个误导性很强 —— 人会去数括号，而不是去查编码。

**防止复发**：`scripts\*.ps1` 必须以 UTF-8 with BOM 保存。
用编辑器保存时注意选带 BOM 的 UTF-8，
或者直接跑这个脚本（它同时做语法检查）：

```powershell
powershell -File scripts\add-bom.ps1              # 处理 scripts\*.ps1
powershell -File scripts\add-bom.ps1 某文件.ps1   # 处理指定文件
```

> ⚠️ 它**只加 BOM，不掩盖别的编码问题** ——
> 若文件不是合法 UTF-8，它会报错并跳过，
> 而不是硬加 BOM 让问题更难查。

**改完 `.ps1` 必须复查**：BOM 还在、语法能过。
`add-bom.ps1` 两者都做，因此改完脚本跑一次它就够了。

### 8.2 `Start-Process` 不要配 `-RedirectStandardOutput`

它会让 PowerShell **等进程结束**再继续（因为它要读完那个流）。
而后端与前端都是**长期运行**的进程 ——
于是启动脚本会**永远卡住**，而服务其实已经起来了
（日志里能看到 `Uvicorn running on …`）。

`scripts\start-all.ps1` 的做法是交给 `cmd /c` 自己重定向：

```powershell
Start-Process -FilePath "cmd.exe" `
    -ArgumentList @("/c", "uv run uvicorn … > `"$LogDir\backend.log`" 2> `"…`"") `
    -WorkingDirectory $BackendDir -WindowStyle Hidden
```

### 8.3 `docker compose` 的进度输出会走 stderr

PowerShell 会把原生命令的 stderr 当作 `NativeCommandError`。
若脚本设了 `$ErrorActionPreference = "Stop"`，
**一句进度信息就会终止整个脚本**（实测踩过）。

因此在调用 `docker compose` 时临时放宽该设置，
改用 `$LASTEXITCODE` 判断真实成败 ——
**不要**为此把全局设置改成 `Continue`，那会让真正的失败被静默跳过。

### 8.4 后台进程活不过一轮对话

AI 助手在会话里启动的服务**挂在会话进程树上**，
会话结束就被清理。因此启动服务请**自己执行** `start-all.ps1`，
它启动的进程是独立的。

---

---

## 九、当前状态

- **MVP 已达成**：上传资料 → 开始面试 → 逐题作答（含自动追问）
  → 结束 → 报告，七个页面覆盖全链路
- **检索基线**：文档级 R@1 = 0.957，而 chunk 级 R@1 = 0.609
  （见计划书 §8.5）—— 主要瓶颈在"找对段落"
- **异步文档索引**：上传即返回 202，嵌入由后台 worker 完成
- 完整进度、待办与缺陷记录见 `MIND_FLOW_PLAN.md`
