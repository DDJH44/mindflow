# MindFlow 项目总规划

> **项目北极星：** MindFlow 是面向求职者的、基于个人资料知识库与 RAG 的 AI 面试助手。它根据用户真实的简历、岗位 JD、项目材料和代码材料，生成可追溯、可追问、可评估的个性化模拟面试体验。

- 文档状态：项目总指挥文档（Source of Truth）
- 最后更新：2026-10-03
- 当前阶段：**Phase 5 — Interview Engine MVP** 🟡
- 本文档约定：所有新模块在开始前必须先对照本文件的产品目标、阶段出口条件与 ADR；完成后更新相应状态和证据。
- 本次更新说明：Phase 4（RAG Evaluation）已完成出口条件并关闭；Phase 5 已推进到评价体系与 Interview Evaluation Framework。本次同步代码实际进度、ADR 状态、待办清单与遗留技术债。

## 状态说明

| 标记 | 含义 |
| --- | --- |
| ✅ | 已实现，并已有可运行代码或验证脚本 |
| 🟡 | 当前正在进行，尚未达到阶段出口条件 |
| 🔵 | 已确定设计，等待后续阶段实现 |
| 🔴 | 明确不在当前范围内，或尚未决定是否投入 |

---

## 1. 项目定位与边界

### 1.1 要解决的问题

一般的面试题库或通用聊天机器人不了解候选人的实际经历，容易提出模板化问题，也无法判断回答是否与材料一致。MindFlow 将用户自己的资料处理为受权限隔离的知识库，在面试过程的每次提问、追问和评价中提供有证据的上下文。

### 1.2 产品定位

MindFlow 的核心能力是：

1. 管理个人面试材料：简历、JD、项目说明、代码和补充材料。
2. 从材料中检索与当前面试主题相关的证据片段。
3. 根据岗位目标、候选人经历和回答过程生成个性化问题及追问。
4. 对回答进行有依据的反馈，并生成可复盘的面试总结。

### 1.3 非目标与边界

MindFlow **不是**通用多 Agent 平台。当前不建设以下能力：

- 🔴 面向任意任务的 Agent 市场、Agent 编排平台或通用工作流引擎。
- 🔴 无边界的互联网知识问答；检索的第一优先级是用户授权的个人资料。
- 🔴 以自动替代真实招聘决策为目标的筛选系统。
- 🔴 未经用户确认就跨项目、跨用户共享材料或检索结果。

未来即使引入多个内部角色（Planner、Question Generator、Evaluator），它们也只是服务于同一条面试工作流，而不是独立产品形态。

### 1.4 成功标准

- 用户能基于自己的资料完成一轮模拟面试。
- 每个关键问题、追问或评价能关联到相应资料证据，或明确标注为通用能力问题。
- 资料、项目、检索和会话始终遵守用户边界。
- 通过 Retrieval Evaluation 量化检索质量，再让 Interview Engine 依赖检索结果。

---

## 2. 核心用户流程

```text
注册 / 登录
    ↓
创建项目（例如：后端工程师求职）
    ↓
上传并标注资料类型
  ├─ resume（简历）
  ├─ jd（岗位描述）
  ├─ project（项目材料）
  ├─ code（代码材料）
  └─ other（其他）
    ↓
解析 → 切块 → Embedding → Milvus
    ↓
建立或选择面试目标
    ↓
检索资料证据 + 生成问题
    ↓
候选人回答 → 评估 → 追问 / 下一题
    ↓
面试总结、能力画像与改进建议
```

当前实现到“解析、切块、向量化与检索基础能力”；面试会话与评价仍在规划阶段。

---

## 3. 产品模块地图

| 模块 | 职责 | 状态 |
| --- | --- | --- |
| 账户与认证 | 注册、登录、JWT 身份识别 | ✅ |
| 项目管理 | 以项目隔离用户材料与未来会话 | ✅ |
| 文档中心 | 上传、类型标注、解析、切块、状态管理 | ✅ 基础版 |
| 知识索引 | Embedding、Milvus 向量写入、元数据 | ✅ 基础版 |
| 检索服务 | Query embedding、过滤、Milvus 召回、回填 Chunk 正文 | ✅ 基础版 |
| Retrieval Evaluation | 数据集、Runner、指标、报告与回归门禁 | ✅ v1（出口条件已满足） |
| Interview Engine | 面试规划、题目、追问、评价、总结 | 🟡 MVP 已可用，评价体系与评估框架进行中 |
| 面试会话 | 会话状态、问题与回答持久化、暂停恢复 | 🟡 持久化 ✅ / 状态机 🔵 |
| Interview Evaluation | 面试引擎质量数据集、规则判定、报告、稳定性基线 | 🟡 进行中 |
| 反馈与成长 | 能力画像、错题、训练建议、历史对比 | 🔵 |
| 平台治理 | 监控、审计、限流、备份、数据生命周期 | 🔵 |

---

## 4. 总体技术架构

```text
┌──────────────────────────┐
│         Web Client        │
│ Vue / Vite（当前基础界面） │
└────────────┬─────────────┘
             │ HTTPS + Bearer JWT
┌────────────▼─────────────┐
│       FastAPI Backend     │
│ routers → services → repos│
└───────┬─────────┬────────┘
        │         │
        │         ├──────────────► Redis
        │         │                 缓存 / 任务协调（后续扩展）
        │
        ├────────────────────────► PostgreSQL
        │                           用户、项目、文档、Chunk、会话事实源
        │
        ├────────────────────────► Qwen DashScope-compatible API
        │                           text-embedding-v4（1024 维）
        │
        └────────────────────────► Milvus 2.5
                                    mindflow_chunks_v2
                                    相似度检索与元数据过滤
```

### 4.1 分层原则

| 层 | 当前职责 | 规则 |
| --- | --- | --- |
| Router / API | 鉴权、请求校验、HTTP 语义 | 不直接写复杂业务或 Milvus 调用 |
| Service | 文档处理、Embedding、检索、未来面试编排 | 组合抽象服务与仓储 |
| Repository | SQLAlchemy 数据访问 | 只处理 PostgreSQL 实体访问 |
| Model / Schema | 持久化模型与输入输出契约 | 数据迁移必须同步维护 |
| Integration | Qwen、Milvus、Redis 等外部服务 | 经由接口或适配层隔离 |

### 4.2 当前基础设施

- ✅ FastAPI、异步 SQLAlchemy、`asyncpg`、PostgreSQL 基础工程。
- ✅ Redis 已在 Docker Compose 中配置；业务级缓存、队列与限流尚未接入。
- ✅ Milvus standalone、etcd、MinIO 已在 Docker Compose 中配置。
- ✅ 配置由 Pydantic Settings 和 `.env` 驱动；密钥不应提交到代码仓库。

---

## 5. 数据层与数据模型

### 5.1 当前持久化模型

| 实体 | 关键字段 | 用途 | 状态 |
| --- | --- | --- | --- |
| `User` | `id`、`username`、`email`、`password_hash` | 账号与身份 | ✅ |
| `Project` | `id`、`owner_id`、`name`、`description`、`status` | 用户资料与未来会话的隔离边界 | ✅ |
| `Document` | `project_id`、`document_type`、`content`、`status`、文件信息 | 原始材料及处理状态 | ✅ |
| `DocumentChunk` | `id`、`document_id`、`chunk_index`、`content`、`chunk_metadata`、`embedding_status` | RAG 的最小正文单元 | ✅ |

### 5.2 文档类型

`Document.document_type` 当前约束为：

```text
resume | jd | project | code | other
```

该字段已存储在 PostgreSQL，并复制到 Milvus 元数据中，用于检索过滤。历史文档迁移时归类为 `other`。

### 5.3 当前状态字段

| 字段 | 当前值 / 语义 |
| --- | --- |
| `Document.status` | `pending`、`parsed`、`chunked`、`failed` 等文档处理状态 |
| `DocumentChunk.embedding_status` | `pending`：待向量化；`embedded`：Milvus 写入成功后标记 |
| `Project.status` | 当前项目生命周期状态，默认 `active` |

### 5.4 面试数据模型（已实现）

面试相关表已由迁移 `8923b9720866_add_interview_tables.py` 建立，当前实体如下。
**字段设计相对原规划有简化**，差异记录在"与规划的偏差"一列：

| 实体 | 当前关键字段 | 用途 | 与规划的偏差 |
| --- | --- | --- | --- |
| `InterviewSession` | `project_id`、`status`、`interview_type`、`target_role`、`plan_snapshot`、`current_question_index` | 一轮面试的状态机与上下文快照 | 缺 `user_id`（当前靠 `project_id` 间接约束所有权）；状态与计划快照列已建立（迁移 `c7d2e5f8a1b4`） |
| `InterviewQuestion` | `session_id`、`question`、`question_type`、`question_index`、`context`、`evidence_chunk_ids`、`is_general` | 每道题及其资料依据 | 缺 `category`（由 `question_type` 承担）、缺 `sequence`（由 `question_index` 承担）；**资料依据已落库** |
| `InterviewAnswer` | `question_id`、`answer` | 用户回答事实记录 | 缺 `submitted_at` 的显式字段（依赖 `created_at`） |
| `InterviewEvaluation` | `session_id`、四项 score、`feedback`、`strengths`、`weaknesses`、`suggestions`、`scoring_details` | 整场面试评价 | 缺 `AnswerEvaluation` 的"逐题评价"粒度；结构化内容与评分明细已落库（迁移 `b41f7c9a2e38`） |

未实现的规划实体：

- 🔵 `AnswerEvaluation`（逐题评价与证据）
- 🔵 `InterviewSummary`（总结与训练建议）

> ⚠️ 上表"与规划的偏差"是当前代码的真实状态，不是待办承诺。
> 其中 **`user_id` 是进入 Phase 6 前唯一仍需补齐的字段**；
> `evidence_chunk_ids` 已随迁移 `c7d2e5f8a1b4` 落地。

---

## 6. 文档处理 Pipeline

### 6.1 当前流程

```text
POST /api/projects/{project_id}/documents
    ↓
项目归属校验
    ↓
保存上传文件
    ↓
创建 Document（document_type 已记录）
    ↓
DocumentParser
    ↓
ChunkingService（默认 500 字符 / 100 字符 overlap）
    ↓
创建 DocumentChunk（embedding_status=pending）
    ↓
Document.status = chunked
```

- ✅ 解析器支持 `.txt` / `.md` / `.pdf` / `.docx`（ADR-040）。
- 🔵 代码结构解析、表格与 OCR 将按用户需求扩展；不以"支持文件数量"为目标，而以可检索、可引用的内容质量为目标。
- ✅ 上传 Router 现在结束于 `embedded`：解析 → 切块 → 写入向量索引在同一次请求内完成。
  嵌入**非致命**（失败保留 `chunked` 并返回 502），用户可在资料页点「重试索引」。
  后续若引入异步任务队列，应改为"上传即返回、后台索引"，避免同步请求被模型调用阻塞。

### 6.2 已知性能问题：Performance Refactor

`DocumentChunkRepository.create_chunks()` 在批量 `add_all` 与 `commit` 后，对每一个 Chunk 调用一次 `refresh()`。当文档产生 N 个 Chunk 时，会形成 **N+1 数据库访问**。

- 状态：🔵 已记录，**本阶段不修改代码**。
- 未来方案：评估批量 `INSERT ... RETURNING`、必要字段的批量查询或只在调用方真正需要 ID 时查询。
- 验收标准：大文档切块不会因逐条 refresh 造成线性额外 SQL 往返，且返回对象语义保持兼容。

---

## 7. RAG 架构

### 7.1 索引 Pipeline

```text
pending DocumentChunk 列表
    ↓
OpenAIEmbeddingService
    ↓  Qwen DashScope-compatible endpoint
text-embedding-v4 / 1024-dimensional vectors
    ↓
MilvusVectorStore.create_collection()
    ↓
MilvusVectorStore.insert() / upsert()
    ↓
Milvus collection: mindflow_chunks_v2
    ↓
PostgreSQL: embedding_status = embedded
```

### 7.2 现有抽象

| 抽象 | 实现 | 目的 | 状态 |
| --- | --- | --- | --- |
| `EmbeddingService` | `OpenAIEmbeddingService`、`MockEmbeddingService` | 隔离 Embedding 供应商 | ✅ |
| `VectorStore` | `MilvusVectorStore` | 隔离向量库实现 | ✅ |
| `EmbeddingPipelineService` | 组合文档仓储、Embedding 与 VectorStore | 对 pending Chunk 批量索引 | ✅ |
| `RetrievalService` | Query embedding + Milvus + DB 回填 | 返回可供上层使用的 Chunk 内容 | ✅ |

### 7.3 Milvus V2 设计

- ✅ Collection：`mindflow_chunks_v2`。
- ✅ 主键：PostgreSQL `DocumentChunk.id`，类型 `INT64`，不自动生成。
- ✅ 向量字段：`vector`，`FLOAT_VECTOR`，维度 1024。
- ✅ 指标：`COSINE`，索引 `AUTOINDEX`。
- ✅ 元数据：`document_id`、`project_id`、`document_type`。
- ✅ 写入策略：`upsert`；同一 `chunk.id` 的重试覆盖旧向量，避免主键冲突。

### 7.4 检索流程

```text
用户问题
    ↓
Qwen Query Embedding
    ↓
构造过滤条件
  project_id == ... AND document_type == ...
    ↓
Milvus similarity search
    ↓
按 Milvus 返回的 chunk_id 从 PostgreSQL 批量取正文
    ↓
按相似度原始顺序回填为 RetrievalResult
```

当前 `RetrievalService.retrieve()` 支持：

- ✅ `limit`
- ✅ `project_id` 过滤
- ✅ `document_type` 过滤
- ✅ 以 `DocumentChunk` 正文作为返回内容，避免将正文重复存入 Milvus
- 🔵 混合检索、重排（rerank）、查询改写、上下文压缩、来源多样性控制

### 7.5 RAG 设计原则

1. PostgreSQL 是业务事实源；Milvus 是可重建的索引派生数据。
2. 每次检索必须在允许的项目与文档类型范围内发生。
3. Interview Engine 应接收结构化检索结果和证据 ID，而不是只接收模型自由生成的文本。
4. 没有足够资料证据时，应允许 Agent 表达不确定性，不得伪造候选人经历。

---

## 8. Retrieval Evaluation

### 8.1 当前阶段目标：Phase 4 🟡

在进入 Interview Engine 前，先验证 RAG 检索能否稳定找回正确资料。当前已有初版 `app/evaluation/datasets.py`，每个 case 包含：

```text
id, query, expected_document_ids, expected_chunk_ids,
project_id, document_type, difficulty
```

当前样例覆盖简历中的技术栈、项目、RAG 技术和岗位方向问题。

### 8.2 下一步：Evaluation Runner

状态：🟡 **下一项唯一的阶段开发任务**。

Runner 必须：

1. 遍历 `EVALUATION_CASES`。
2. 使用真实 `RetrievalService` 及 case 的 `project_id`、`document_type`。
3. 保存每个 case 的 Top-K chunk / document、分数、耗时、过滤条件和版本信息。
4. 输出结构化 JSON 报告与人类可读摘要。
5. 不改写资料、不创建生产数据、不掩盖失败样例。

### 8.3 基础指标

| 指标 | 定义 | 目的 |
| --- | --- | --- |
| Recall@1 | Top 1 是否包含任何期望 Chunk（或文档） | 最佳结果是否可靠 |
| Recall@3 | Top 3 是否覆盖期望 Chunk（或文档） | 小上下文预算下的召回质量 |
| Recall@5 | Top 5 是否覆盖期望 Chunk（或文档） | 默认检索预算下的召回质量 |
| MRR | 第一个相关结果的倒数排名 | 相关证据是否靠前 |
| Filter accuracy | 结果是否全部满足项目和资料类型过滤 | 权限 / 元数据正确性 |
| Latency | embedding、Milvus、DB 回填、总耗时 | 性能基线 |

初期应同时报告 **chunk-level** 和 **document-level** Recall，避免“召回到同一文档的相邻 Chunk”被误判为完全成功。

### 8.4 Phase 4 出口条件

- [x] Evaluation Runner 可重复运行（`app/evaluation/runner.py`）。
- [x] 输出 Recall@1、Recall@3、Recall@5、MRR、Filter accuracy 和延迟。
- [x] 每一个失败 case 都能定位 query、期望 ID、实际结果与过滤条件（报告含 `failures` 与逐 case 明细）。
- [x] 建立首份基线报告：`evaluation_reports/rag_evaluation_*.json` 共 5 份（2026-09-23）。
- [x] 覆盖 resume、other 的有效样例，并在报告 `data_coverage` 中明确记录 jd / project / code 的缺失原因。

**Phase 4 已于 2026-09-23 关闭。** 关键证据：5 份 RAG 评估报告落盘，chunk-level 与 document-level Recall / MRR / Filter accuracy 均有记录，缺失资料类型的原因写在报告里而非被静默忽略。

已知遗留（不阻塞关闭，登记为后续项）：

- 🟡 数据集仅 7 个 case，覆盖 `resume` 与 `other` 两类；jd / project / code 需在真实资料上传后补齐样例。
- 🟡 `app/evaluation/runner.py` 的 `save_report` 中 `retrieval_config` 与 `data_coverage` 为硬编码，扩充数据集时需同步维护，否则报告会与实际不一致。

---

## 8A. Interview Evaluation（面试引擎评估）

RAG 评估回答的是“**检索找得准不准**”；这一节回答的是“**面试引擎问得好不好、评得稳不稳**”。
两者共用 `evaluation_reports/` 目录，但**代码分层与报告前缀严格区分**，不得混放：

| | RAG Evaluation | Interview Evaluation |
| --- | --- | --- |
| 代码 | `app/evaluation/` 顶层 | `app/evaluation/interview/` |
| 报告前缀 | `rag_evaluation_*.json` | `interview_evaluation_*.json`、`interview_stability_*.json` |
| 入口 | `python -m app.evaluation.runner` | `python -m app.evaluation.interview.cli` |
| 成本 | Embedding + Milvus | LLM（每轮 2 次调用） |

### 8A.1 为什么必须固定输入

数据集里的问题与回答**全部写死**，不使用 LLM 现场生成。原因：

- 输入固定后，分数变化才能归因到 Prompt / 模型 / 逻辑，而不是“这次生成的问题不一样”。
- 可复现、可存档、可做跨版本基线对比。

### 8A.2 数据集结构

```python
{
    "id": "rag_backend_interview",
    "title": "RAG 后端候选人面试",
    "project_id": 3,
    "direction": "考察候选人的后端开发能力和项目实践经验",
    "turns": [
        {
            "question": "...",
            "answer": "...",
            "expected_missing_points": [...],      # 期望被识别出的技术缺口
            "forbidden_missing_points": [...],     # 否定模式：不该出现的“整条链路都没讲”
            "expected_follow_up_keywords": [...],  # 追问应命中的技术点
            "min_analyzer_score": 45,              # 该轮评分的合理下限
        },
    ],
}
```

### 8A.3 判定规则

| 规则 | 判定内容 | 实现 | 状态 |
| --- | --- | --- | --- |
| **R1** 缺口命中 | `expected_missing_points` 至少命中 1 个；为空则视为无需命中 | `evaluate_missing_point_coverage` | ✅ 使用中 |
| **R3** 追问有针对性 | `expected_follow_up_keywords` 至少命中 1 个，且不是"请详细介绍一下"这类空话 | `evaluate_follow_up_targeting` | ✅ 使用中 |
| **R4** 评分下限 | 三项评分不低于该轮 `min_analyzer_score` | `evaluate_analyzer_score_floor` | ✅ 使用中 |
| ~~R2~~ 不冤枉 | ~~`forbidden_missing_points` 一个都不命中~~ | `evaluate_forbidden_missing_points` | 🔴 **已废弃** |

**R2 为何废弃（ADR-018）：**

原意是防止评估器把"链路完整的回答"误判为"整条链路都没讲"。
但该判据**在原理上无法成立** —— 任何"未说明 X"模式都能被正当用来
批"X 讲得不够深"。实测三次误报：

| 次数 | 判据写法 | 误报命中的句子 | 实际情况 |
| --- | --- | --- | --- |
| 1 | 技术名词（`Milvus` / `Chunk`） | "Embedding 与 Milvus 细节缺失" | 批的是"细节不足"，合理 |
| 2 | `未说明分块` | "未说明 500 字符分块的**参数依据**" | 批的是"依据没讲"，合理 |
| 3 | `未说明简历解析` | "未说明简历解析的**具体实现**" | 批的是"实现没讲"，合理 |

要区分"整条链路没讲"与"讲了但不够深"，只能靠**语义理解**；
在评测框架里再套一次 LLM 判定，会把评测的可靠性建立在被评测对象上。

**R2 同时是冗余的。** 它要防的失败模式已由 **R4** 覆盖
（analyzer 若真的冤枉完整回答，评分会掉到门槛以下）。
实测印证：`rag_backend_interview` turn 1 报出 **10 条缺口**，
但 `answer_quality = 90` —— 说明
**`missing_points` 的详略与评分高低是解耦的**，不应据此判违规。

结论：`forbidden_missing_points` 已从数据集移除；
汇总时废弃规则**不计入通过率**，避免用无法成立的判据制造假失败。

### 8A.4 四条已验证的数据集编写约束（踩过的坑，必须遵守）
**约束一：`expected_follow_up_keywords` 必须含语义等价词。**

- ❌ 只写 `["chunk", "metadata", "字段"]`
- ✅ 补充 `["标识", "对应", "映射"]`

原因：实测追问会用
`“你用什么唯一标识把 Milvus 中的向量和 PostgreSQL 中的文本记录对应起来？”`
表达 chunk_id 那层关联。只列字面词会把**正确追问误判为跑偏**。

**约束二：`expected_missing_points` 要覆盖中英混用写法（D27）。**

- ❌ 只写 `["切分长度"]`
- ✅ 补充 `["chunk 长度", "切块长度", "切分粒度"]`

原因：模型经常把"切分"写作 "chunk"，例如
`“未说明 chunk 长度的具体取值以及为什么选择这个长度”`。
`ALIASES` 里缺这条等价关系时，**正确识别出的缺口会被判成未命中**。
实测该问题让一个 case 的 5 个期望全部落空。

**约束三：`expected_missing_points` 不要写"元评价词"（D28）。**

- ❌ 写 `["未回答", "回避"]`
- ✅ 写 `["切分策略", "切分长度", "切分依据", "参数"]`

原因：实测模型**不会**输出"候选人回避了问题"这类判断，
而是直接列出"一个合格回答本该覆盖什么"。
两种表达都正确，但用元评价词做期望会把正确行为判成未命中。
判断回答质量应依赖**评分**（R4），而不是依赖缺失点里是否出现批评性措辞。

判定内部使用 token 序列匹配 + 别名表（`ALIASES`），
`chunk_id` / `chunk id` / `ChunkID` 视为同一概念；
`metadatas` 不会命中 `metadata`。
`app/evaluation/interview/judges.py` 内置 16 项自测与数据集一致性检查，
直接运行即可验证（`python -m app.evaluation.interview.judges`）。

**约束四：评分门槛（`min_analyzer_score`）必须按锚点分标定，不能凭感觉设。**

评分改为锚点制后（ADR-016），分数只能取以下离散值：

```text
A=90  B=70  C=40  D=15
```

因此 `min_analyzer_score` 只能取这些值之一，或 `0` 表示不校验。
例如：

- 表示"至少要达到 B 档" → `70`
- 表示"至少要达到 C 档" → `40`

不要写 45、55、65 这类值，它们不对应任何档位，
会导致判定结果无法解释。

### 8A.5 稳定性基线（实测）

同一输入、同一 `temperature=0.2`、`deepseek-flash`、`--repeats 10`（**覆盖率 100%，无样本丢弃**）：

| case / turn | 回答性质 | `answer_quality` 均值 | `technical_depth` 均值 | 最大极差 |
| --- | --- | --- | --- | --- |
| `evasive_answer_interview` turn 1 | 答非所问 | 6.0 | 0.0 | **10** |
| `rag_backend_interview` turn 1 | 简略但完整 | 68.8 | 49.7 | **17** |
| `rag_backend_senior_interview` turn 1 | 完整专业 | 90.0 | 84.0 | **18** |
| `redis_cache_follow_up_interview` turn 2 | 追问后补全 | 89.1 | 81.5 | **10** |
| `redis_cache_follow_up_interview` turn 1 | 一句话 | 37.0 | 15.0 | **15** |
| `rag_backend_interview` turn 2 | 一句话 | 36.0 | 15.0 | **20** |

**6 轮中只有 1 轮（evasive）通过 15 分阈值，其余 5 轮均被判为不稳定。**

**B. 锚点化之后（ADR-016）**

18 个"轮次 × 维度"的对照统计：

| 指标 | 锚点化前 | 锚点化后 |
| --- | --- | --- |
| 平均极差 | 10.7 | **4.4** |
| 平均标准差 | 3.49 | **1.82** |
| 完全稳定的维度数 | 1 / 18 | **15 / 18** |

**锚点化把平均标准差降低了约一半，完全稳定的维度从 1 个增加到 15 个。**

**C. 结论（重要）**

1. **自由打分时，评分波动与回答质量基本无关。**
   均值 6 到 90 的回答，极差都落在 **10–20**。
2. **锚点化后残余波动集中在档位边界。**
   剩余不稳定项全部出现在 `answer_quality` 的 B(70) / C(40) 之间，
   说明残余波动来自**锚点描述的边界模糊**，而非模型随机性。
   对策是收紧锚点描述（已加入"有没有直接回应提问"的判定总则）。
3. **15 分阈值在锚点化后已基本可用**，
   但档位边界造成的跳档是 20–30 分，仍需通过锚点描述而非阈值来解决。
4. `seed` 参数被端点接受但不保证收敛（5 样本下无统计意义）。
5. **不得**以评分差异作为质量门禁，只能以 R1 / R3 / R4 的**结构性判定**为准（见 ADR-015）。
   （原 R2 已废弃，见 ADR-018。）
6. 稳定性的可信度**依赖样本覆盖率**：样本未跑满时，成功样本的极差会天然偏小，
   从而给出"稳定"的错误结论。框架因此加入覆盖率门槛（见 §8A.7 D9）。

> ⚠️ **一次被推翻的结论，记录在此以免重犯。**
> 在覆盖率门槛（D9）修复**之前**的那次 `--repeats 10` 运行中，
> `rag_backend_interview` 有 3–6 个样本因传输错误被静默丢弃。
> 当时基于那批脏数据得出过"波动与回答质量的极端程度成反比、
> 中间质量最不稳定"的结论。
> 修复重试与覆盖率门槛后（覆盖率 100%），重新测量发现：
> **均值 90 的完整回答极差同样达到 18**，原结论不成立。
> 教训：**在统计口径未验证可信之前，不要基于指标下结论。**

### 8A.6 上界对照的标定结果

新增 `rag_backend_senior_interview` 后，同一次运行内的对照：

| 回答 | `answer_quality` | `technical_depth` | `completeness` | 缺口数 |
| --- | --- | --- | --- | --- |
| 完整专业（senior） | 88 | 84 | 86 | 6 |
| 简略但完整（rag turn 1） | 70 | 45 | 55 | 10 |
| 追问后补全（redis turn 2） | 78 | 72 | 75 | 7 |
| 一句话（redis turn 1） | 35 | 15 | 15 | 10 |
| 答非所问（evasive） | 10 | 0 | 5 | 7 |

三点验证成立：

1. **上界对照生效**：完整回答的技术深度比简略回答高 **39 分**，说明分析器没有冤枉完整回答。
2. **回避模式可识别**：`technical_depth = 0` 且 10 次重复零波动，
   追问正确地要求"选一个你实际负责的项目具体说明"，不接受"看具体情况"这类搪塞。
3. **追问链有区分度**：同一 case 内，追问后补全的回答从 `35/15/15` 升到 `78/72/75`。

`rag_backend_senior_interview` 的 `min_analyzer_score` 按实测标定为 **70**
（观测均值 82–89，留约 15 分余量）。

### 8A.7 已知缺陷与修复记录
以下问题全部由实际运行暴露，均已修复。**这些是编写新 case 时必须遵守的前置约束。**

| 编号 | 现象 | 根因 | 修复 |
| --- | --- | --- | --- |
| D1 | `RuntimeError: LLM 返回内容为空` | `generate()` 遇空正文直接抛错，无重试 | 空响应 / 截断重试（`MAX_ATTEMPTS`） |
| D2 | 传输抖动导致样本失败 | 重试未覆盖 `APIConnectionError` / `APITimeoutError` / `RateLimitError` / `5xx` | 传输层异常纳入重试，指数退避 2/4/8/16s 封顶 20s |
| D3 | JSON 解析被围栏与截断打断 | 直接 `model_validate_json`，无剥离、无修复、`max_tokens` 偏小 | 新增 `app/core/model_json.py`：剥围栏 + 提取最外层 JSON + 按状态补齐截断；`max_tokens` 提到 4000 |
| D4 | R2 恒失败，无论怎么作答 | 违规词用技术名词；模型会输出"Embedding 与 Milvus 细节缺失"这类**含技术名词的合理缺口** | R2 改为否定模式判据（ADR-013） |
| D5 | R3 把正确追问判为跑偏 | 关键词只列字面词，未含语义等价词 | 补 `标识` / `对应` / `映射` 等别名，并写入数据集 |
| D6 | 数据集自相矛盾（必然失败） | 同一轮 `forbidden` 与 `expected` 指向同一概念 | `judges.py` 内置数据集一致性检查 |
| D7 | 别名表查不到（漏判） | `ALIASES` 的 key 未归一化，与查询用的归一化 key 不一致 | 统一 key 为归一化写法 |
| D8 | 子串匹配误判 | `id` 命中 `consider` 之类子串 | 改为 token 序列匹配 + 中文字串匹配 |
| D9 | **样本不足仍宣称"稳定"** | 统计只取成功样本，失败样本被静默丢弃；少量样本极差天然偏小 | 新增 `MIN_COVERAGE = 0.8` 覆盖率门槛；不足时判定为 `inconclusive`，退出码 2 |
| D10 | 单次采样在档位边界随机跳档，同一输入极差 20–30 分 | 让模型直接选档位仍存在边界模糊；`temperature=0.2` 无法消除 | ADR-017：默认采样 3 次并按多数档位合并，实测降至 0 极差 |
| D11 | **`missing_points` 条数膨胀（6 → 19 条）** | 多次采样的缺口**取并集**，同一缺口的多个同义说法被全部保留 | ADR-019：字符 bigram + Dice 聚类去重 + 包含抑制；提示词同时要求"最多 6 条且内部不得重复" |
| D12 | 整场评价仍为自由打分，分数不可复现 | 锚点制（ADR-016）只应用到逐轮分析，未同步到 `evaluator.py` | 整场评价也改为锚点制 + 3 次采样合并；实测 3 轮四项分数极差为 0 |
| D13 | 完整的 JSON 被误判为截断，触发无谓重试 | 截断判据写成"以 `{` 开头且不以 `}` 结尾"，把"JSON 完整、结尾带说明文字"误判为截断 | 改以 `finish_reason == "length"` 为主依据，括号配平仅作兜底；12 项单元验证通过 |
| D14 | 整场评价频繁触发 `finish_reason=length` | `max_tokens=4000` 偏小；整场评价的 strengths / weaknesses / suggestions 各约 10–13 条 | `max_tokens` 提到 8000 |
| D15 | **R2 规则三次误报**，导致正确回答被判违规 | `forbidden_missing_points` 原理上无法成立（详见 §8A.3） | 废弃 R2（ADR-018），字段从数据集移除，废弃规则不计入通过率 |
| D16 | 报告无法自证"用了几次采样" | 稳定性模块只记录自己的 scores，未记录分析器的 `sample_count`，导致无法区分 samples=3 与 samples=5 的报告，只能靠时间戳猜 | 报告中新增 `analyzer_sample_count` 与每轮的 `analyzer_sample_counts` |
| D17 | **报告无法诊断去重是否生效** | 只记录去重后的 `missing_points`，看不到每个样本各报了什么，无法判断条数是被合并还是被上限截断 | `AnswerAnalysis` 新增 `sampled_missing_points`（去重前的每样本缺口），并写入报告 |
| D18 | 6/6 轮的缺口数**都恰好等于 `max_items`** | `dedupe_paraphrases` 的 `max_items=10` 成了实际约束，把真实条数差异掩盖成常量 | 经实测标定后把 `max_items` 调到 6（与提示词上限对齐，不再掩盖），相似度阈值 0.55 → 0.35 |
| D19 | 词面去重无法达到真实主题数 | 中文同义复述用词差异大，字符 bigram 重合度低。实测 18 条（真实主题 6）时，阈值低到 0.30 也只能降到 8 条 | 未根治（需 embedding 语义聚类，属新架构决定）。已如实记录能力边界，并把目标改为"压到可读的个位数" |
| D20 | **`strengths` / `weaknesses` / `suggestions` 未持久化** | 只存在于 LLM 返回值中，`evaluation_service` 落库时只写四项分数与 `feedback`。这是 Phase 6「能力画像与训练建议」的前置数据 | 迁移 `b41f7c9a2e38`：新增 `strengths` / `weaknesses` / `suggestions` / `scoring_details` 四个 JSON 列并落库；已验证 upgrade / downgrade 往返与端到端写入 |
| D21 | 线上与离线分数不可比，但历史评分只留一个数字 | ADR-021 承认两条路径采样数不同，因此同一回答的分数可能不同。只存分数则事后无法判断差异来源 | `scoring_details` 记录锚点、`anchor_reason`、采样次数、每次采样分数与口径快照（`path` / `samples` / `model`） |
| D22 | **资料依据完全丢失** | `context_builder.build_context` 把检索结果装进 dict 时丢掉了 `chunk_id`，导致"这道题凭什么这么问"无法回答（违反 §9.3 与产品北极星第 1 条） | `collect_evidence_chunk_ids` 在检索现场捕获并落库；新增 `evidence_chunk_ids` 与 `is_general` 列（迁移 `c7d2e5f8a1b4`） |
| D23 | `_format_context` 与证据提取的**顺序靠巧合一致** | 前者用 dict 插入顺序、后者用 `DOCUMENT_TYPES`。当前 `build_context` 恰好按 `DOCUMENT_TYPES` 构建所以一致，但一旦构建顺序改变，落库证据就会与实际依据不符且不报错 | 两者统一显式遍历 `DOCUMENT_TYPES`，把顺序变成强约束 |
| D24 | 会话状态只有一个裸字符串，无任何校验 | 任何代码都能把状态写成任意值，历史记录无法解释"会话怎么从 evaluating 直接跳到 completed" | 状态机模块（ADR-022）：显式状态枚举 + 白名单转移表 + 强制校验；Router 对非法转移返回 409 |
| D25 | 迁移前会话的状态值是 `created` | 新状态机以 `draft` 为起点，旧值无对应状态 | `LEGACY_STATUS_MAP` 在读取时兼容，**不做数据回填**（回填会改写历史审计信息）；已在真实数据上验证 2 条旧记录 |
| D26 | **状态机抓到实现 bug**：`asking → evaluating` 被我当成一步 | 状态表里 `asking` 只能到 `waiting_for_answer`，必须两步走。若不是状态机强制校验，这个非法转移会静默写进库，历史记录从此无法解释 | 显式走 `asking → waiting_for_answer → evaluating → asking`，每步都经 `apply_transition` 校验 |
| D27 | **别名表缺中英等价词**，R1 把正确识别的缺口判成未命中 | 模型把"切分"写作 "chunk"（如"未说明 chunk 长度的具体取值"），而 `ALIASES` 里只有"切分长度"，导致 expected 全部落空 | 补 `切分长度/切分依据/切分策略/切分` 的中英等价别名；用真实缺口离线验证由 0/5 提升到 4/4 |
| D28 | 反向踩坑：`expected_missing_points` 写了**元评价词** | 期望列表里写了"未回答""回避"，但实测模型不会输出"候选人回避了问题"，而是直接列出"合格回答本该覆盖什么"。用元评价词做期望会把正确行为判成未命中 | 期望列表改为内容型关键词（`切分策略`/`切分长度`/`切分依据`/`参数`），并把这条写入 §8A.4 约束 |
| D29 | `finish` 端点的状态路径错误：`asking → evaluating` 不是一步 | 与 D26 同类：状态表里 `asking` 的后继是 `waiting_for_answer`，必须经它才能到 `evaluating`。**上一轮的流程测试没覆盖"从 asking 直接结束"这条路径**，所以漏了 | 显式走完整链条；由新写的 API 层测试发现（37 项，含这条） |
| D30 | 编写清理脚本时**用问题文本匹配探针标记，漏判** | 探针标记写在 `target_role` 上而不是问题文本里，导致识别为 0 个残留，清理静默无效 | 改用 `target_role` 精确匹配 + "无任何问题"双重条件；并人工复核了删除范围 |
| D31 | `start` 的状态推进对"已在中间状态"的会话会**回退** | 循环无条件执行 `planned → preparing_context`，而状态机是单向的，回退被拒。调用方从 `planned` 调 `start` 会失败 | 按当前状态决定从哪一步开始：`draft` 走两步、`preparing_context` 走一步、`planned` 不走 |
| D32 | `generate_and_save_question` 用 `assert_transition(status, ASKING)` **校验错了东西** | 它校验的是"能否到达 asking"，而 `asking → asking` 不在转移表里（自己到自己不是转移），会把"已在 asking、要生成下一题"错误拒绝；同时它也没拦住 `draft` 直接生成题目 | 改用显式的 `ALLOWED_SOURCE_STATUSES = {planned, asking}` 白名单校验"当前状态可接受" |
| D33 | **`assert_transition` 只校验、不改状态**，我却用它替代了实际的转移 | 写了两次 `assert_transition` 确认 `asking → waiting_for_answer → evaluating` 可达，然后**直接申请** `EVALUATING` —— 但状态机看到的当前状态仍是 `asking`，而 `asking` 只能到 `waiting_for_answer`。断言通过、转移失败 | 两步都要**执行** `apply_transition`，不能只 assert。已在代码注释中写明这个区别 |
| D34 | 题目预算**漏算追问**，预算形同虚设 | 只在 `generate_and_save_question` 里递增 `questions_asked`，但追问由 `process_answer` 自己调 `create_question` 创建 —— 于是计数永远停在常规题目数，`max_questions=2` 的会话连问 3 题都不停 | 追问生成后同样递增计数；把"预算含追问"写进 ADR-028 |
| D35 | `_session_payload` **漏了新增字段**，终止原因读不到 | 模型加了 `termination_reason`，但路由的响应是内联手工构造的 dict，忘了同步 —— 库里写对了、接口读不到 | 补齐 `max_questions` / `questions_asked` / `termination_reason`。这也是"内联构造响应"的代价，已在 §11 记录 |
| D36 | 配额"先判定再自增"在**并发**下会一起放行 | 两个请求同时读到 `used=9/quota=10`，都认为还有额度 —— 配额在并发下形同虚设。这是设计阶段自查发现的，不是跑出来的 | 改为**先自增再判定**（原子 UPSERT + RETURNING），被拒时回滚 |
| D37 | 配额消费最初放在**创建会话**时，测试累积消耗导致脚本失败 | 一次失败的验证不是代码 bug，而是暴露了产品问题：创建 draft 草稿没有任何 LLM 调用却扣额度，"点了新建又没开始"白损失一场 | 消费点移到**开始面试**；同时让测试自备配额（先清空用量再设固定值），避免累计消耗 |
| D38 | 测试脚本**不自带状态**，依赖"数据库恰好是什么样" | 预算验证在配额累计消耗后失败过一次。测试若依赖外部残留状态，失败原因会指向错误的地方 | 需要特定状态的脚本（如配额测试）在开头 prepare、结尾 restore；并抽出 `app/core/test_support.py` 共享脚手架 |
| D39 | 概率性测试失败时**误判为代码 bug** | 预算测试在配额耗尽时报"作答返回 409"，看起来像状态机坏了，实际是额度用完导致 `start` 未执行、会话停在 draft。**错误信息指向了错误的地方** | 测试自备配额后消除；并把这条记入 §22.3"先怀疑测试与环境，再怀疑实现" |
| D40 | 为"让断言失败也清理"做缩进重构时**用脚本反复改文件，把文件改坏** | 先手工只缩进了部分行（SyntaxError），又用"缩进 < 12 补到 12"的启发式脚本破坏了相对层级，再整段平移一次，导致后续所有代码掉进 `else:` 分支 | 停止脚本化修改，读全文后**一次性重写**。教训：对已损坏的文件用启发式脚本补丁会放大破坏；此时应回到确定性的整体重写 |
| D41 | 测试为"暂停"手工做了两次状态转移，**测试过程本身不自然** | 手工推到 `planned` 用的是 `unspecified` 触发原因，导致 `/start` 发现"已在 planned"、无中间步，`interview_started` 从未被记录、轨迹断言失败 | 测试让主会话走**用户真实路径**（`draft` → `/start` → `asking`），暂停改在 `asking` 上验证 |
| D42 | **换了 LLM 端点，旧的评测分数不再可比** | LLM 从 deepseek-flash 换成 agnes-3.0-flash（ADR-035）。计划书多处用旧模型的分数做论据，若不注明就会变成"用 A 模型的分数证明 B 模型的行为" | 所有历史报告保留原样（它们记录了自己的 model）；§8A 的分数一律标注为"deepseek-flash 口径下测得"，新模型的基线另存（§24.5） |
| D43 | **计划书夸大了评分一致性的实际水平** | §8A.9 写"实测 5/6 轮三项分数在 10 次重复中完全一致，平均极差从 10.7 降到约 1.4"。核查 16 份稳定性报告发现：那是 8 次 100% 覆盖运行里**最好的一次**，而且当时那次还有样本缺口。真实中位数是**稳定 3/6、平均极差约 13** | §8A.9 改写为分布表；§20 对应条目标注。对照实验（10.7 → 4.4）本身有效，被夸大的只是"实际运行水平" |
| D44 | **在测量运行期间修改了被测代码** | 跑 `--stability --repeats 10`（约 30 分钟）时，我中途改了 rubric 源文件。Python 在进程启动时加载模块，因此那次运行的前后两半可能用了不同 rubric，**结果不可比**，只能作废重跑（浪费约 20 分钟） | 长测量开始前冻结代码；必须改时先 kill 再改再跑。已把这条写入 §22.10 |
| D45 | 探针脚本取错字段（取派生分数而非档位字母） | `getattr(analysis, "answer_quality")` 返回数字 70，而我要的是档位 `quality_anchor`。这个错误让我在 §24.5 的表里**混用了数字与档位**（同一列既写 `40/70` 又写 `15/40`） | 已修正 §24.5 的口径标注；探针统一用档位字段 |
| D46 | **用 5 个样本的定向验证宣称稳定性改善** | 定向探针显示 4 个目标轮次 4/4 稳定，我据此写了"0/4 → 4/4"。但全量 `--repeats 10` 的中位数只有 4/6（旧基线 3–4/6）—— **同量级**。`evasive` 的 depth 在探针里 5/5 判 D，实测是 9/10 | §24.9/§24.10 改写：定向验证只说明"我针对的轮次在窄样本下稳定"，整体改善需用全量多次运行的中位数（见 §22.5 样本量原则） |
| D47 | **`QuotaExceeded` 未被路由捕获，500 而非 429** | 题目额度加入后，`consume` 在引擎服务与 turn 服务里抛出 `QuotaExceeded`，但 `POST /{id}/start` 与 `POST /{id}/answer` 都只捕获了 `InvalidTransitionError`/`ValueError`。它继承 `Exception` 而非 `ValueError`，因此直接逃逸成 500 | 两个端点各自捕获并转 429；`start` 因业务数据尚未提交而回滚计数，`answer` 因回答已提交而**不回滚** |
| D48 | 额度扣减与业务数据**跨两个提交**，会出现"题目已存但额度被吞" | 初版把 `consume` 放在 `create_question`（自行 commit）之后。扣减失败时那一次自增会随回滚消失，而题目已经落库 —— 额度少算一题，且用户拿不到提示 | 重排为"**先扣减、后落库**"：LLM 调用后先 `consume`（原子自增+判定），再存题目与状态。这样要么都成功、要么都没发生 |
| D49 | 对比工具的**合成测试数据漏了 case 层 `case_id`**，自测形同虚设 | `_make_report` 只在 turn 上写 `case_id`，case 对象上没有；`index_turns` 以 `case["case_id"]` 为键，于是所有 case 共用 `None` 键互相覆盖，两份报告各只剩一轮。自测因此**看不到档位差异**，测试失败但原因指向断言而非数据 | 补上 case 层 `case_id`；并让自测用例**真的产生档位差异**（原用例只改模型名不改档位，测不到免责分支） |
| D50 | 对比工具**可能拿基线和自己比**，输出"无变化" | 基线指针指向最新报告时，自动选候选会取到"除基线外最新的一份" —— 若指针恰为最新，就变成拿旧报告当候选、反着比；更早的版本直接拿基线和自己比，输出"无变化"，**看起来像"模型没问题"，实际什么都没比** | 自动候选明确"排除基线自己"；并把这条写成自测用例（第 8 项）。另注意：基线应设为**已确立的参照**，而不是最新那份 |
| D51 | **上传资料后从不建立向量索引** —— 一个静默断点 | `upload_document` 只做到解析 + 切块，状态停在 `chunked`；`EmbeddingPipelineService.embed_document()` 只被开发脚本（`core/embed_document_8.py`、`core/test_embedding_pipeline.py`）调用，**没有任何生产入口**。后果：检索恒为空 → 面试只出通用题 → MVP 的"资料依据"落不了地，而用户完全看不出哪里不对 | 上传流程末尾补上嵌入（**非致命**：失败保留 `chunked` 并如实告知）；新增 `POST .../embed` 供重试；新增 `GET .../documents` 让前端能显示状态；`test_document_upload.py` 把"状态必须为 embedded"与"检索能命中"写成断言 |
| D52 | 删除文档会留下**孤儿向量**，静默降低召回 | `document_chunks` 有 `ON DELETE CASCADE`，但 Milvus 里的向量不会被连带删除。而 `RetrievalService.retrieve` 对"Milvus 命中但回表取不到 chunk"的情况只是 `continue` —— **却仍占用 top-k 名额**，于是实际召回数悄悄变少，表现为"检索变差了" | 新增 `DELETE .../documents/{id}`：先删向量再删记录；向量删不掉时返回 502 且**不删记录**（避免制造孤儿） |
| **D53** | **Milvus 一致性级别导致"上传成功但检索不到"** | 集合被创建为默认的 `Bounded` 一致性，**不保证插入后立即可检索**。而 `upload_document` 插入后立刻把文档标为 `embedded` 并返回，调用方紧接着检索就可能落空 → 面试只出通用题。**实测：Bounded 下插入后立刻检索 10 次命中 0 次；Strong 下 10 次命中 10 次**。表现为**间歇性**（复现序列 `[F,F,T,F,T,F]`，2/6 命中），是最难查的一类问题 | ① `create_collection` 显式用 `consistency_level="Strong"`；② `search` 也显式传 `Strong` —— 只改建集合的代码**修不好已存在的老库**，而老库正是问题发生处。修复后复现探针 **8/8 命中**，端到端连续两次 26/26 |
| **D54** | **文档级状态与 chunk 真实情况不符，界面据此误报** | `documents.status` 停在 `chunked`，而其 chunk 已全部 `embedded`。后果：资料页显示"已切块·未索引"、顶部提示"**还没有可被检索的资料**"、首页也警告"问题会退化为通用题" —— 而**向量其实都在，面试确实能检索到**。用户会反复点"重试索引"（对无 pending chunk 的文档返回 400）。这是**看界面才发现的**：接口测试与端到端测试都不检查展示文案 | ① `check_embedded_consistency` 扩为**双向**检查（新增类型 B：文档状态 vs chunk 真实情况），并在修复时对齐文档状态；② 修复后资料页正确显示"已有 3 份资料可被检索" |
| **D55** | **界面承诺支持 PDF / DOCX，后端只支持 TXT / MD** | `DocumentParser` 只实现了 `.txt` / `.md`，而资料页写着"支持 TXT / MD / PDF / DOCX"。**真实用户上传简历两次（PDF 738KB、DOCX 811KB）都被拒**，库里留下两条 `status=failed` 记录，磁盘留下两个文件。这是**最严重的一类问题：入口就断了**，用户不可能走到面试 | ① 实现 PDF（`pypdf`）与 DOCX（`python-docx` + 直读 `document.xml`）解析；② 前端把 `accept` 与后端 `SUPPORTED_SUFFIXES` 对齐，并在上传前做类型/体积检查（不再让 20MB 文件先传完才被拒）；③ 把"解析"前移到**建记录与落盘之前**，失败即彻底不留残留 |
| **D56** | **暂停中无法结束面试**（用户实际报告） | 用户回答一题后暂停，再点"结束并生成报告"得到 `非法状态转移：paused → waiting_for_answer；允许的目标状态为 ['cancelled']`。根因：`finish_interview` 假设会话处于活跃态、无条件推进 `→ waiting_for_answer`，而 `paused` 的唯一合法后继是 `cancelled`。**用户的意图是结束，不是恢复** —— 这个路径此前完全没被考虑 | ① `finish_interview` 遇到 `paused` 时**先恢复再结束**；② 新增 `resolve_resume_target`：`resume_status` 缺失时按问答事实推断恢复目标（有未答题→`asking`，否则→`waiting_for_answer`），把原本**永久卡死**的会话救回来；③ 允许从 `waiting_for_answer` 直接结束；④ 终态重复结束返回 **409** 并说明"已经结束过" |
| **D57** | **上游 LLM 失败变成裸 500，看不出原因** | LLM SDK 的超时/连接异常继承自 `openai.OpenAIError`，**既不是 `ValueError` 也不是 `RuntimeError`**。而生成题目、分析回答、整场评价三处只处理了后两者，于是上游一慢就返回 `{"detail": "Internal Server Error"}` —— 客户端拿不到任何提示，运维也分不清是上游问题还是代码 bug。实测该端点会间歇性慢到超时（极短请求连续 4 次超时、第 4 次重试才在 68.9s 后成功），套件因此偶发崩溃，**看起来像代码回归** | ① 新增 `_llm_error_or_none`：按**类名与 MRO 特征**识别 LLM 异常（不硬绑具体 SDK，便于换端点），映射为 **503** 并带上异常类名与"未写入数据"说明；② `start` 在映射命中时**回滚额度**（避免为一次上游抖动白扣一场）；③ `answer` / `finish` 不回滚（作答可能已提交，回滚会丢用户数据）；④ 测试套件遇到 503 明确报告"未执行"而不是崩溃 |
| **D58** | **前端"草稿"状态下答过题的会话打不开** | 库里存在旧值 `created`（迁移前字面值），而**实际已答过题的会话也可能停在草稿态**。历史页把这些显示为"—"、看起来打不开，而它们恰恰是用户最想找回来的。这是**看截图才发现的**：走查只打印行数，数字上完全看不出 | ① `RESUMABLE` 加入 `created` / `draft`；② 文案改为"未完成"（一场答了 3 题的面试不该叫"草稿"）；③ **给走查补上"每行都必须有可点动作"的断言** —— 否则这类问题下次还是只能靠人眼看出来 |

| **D59** | **嵌入批量上限导致大文件必然上传失败** | 端点单次最多接受 10 条文本，而代码把整个文档的 chunk **一次性**发出 → **超过约 1 万字符（25 块）的文件必然 502**，报错却是"服务不可用"，用户完全看不出真因。实测 200KB 与 800KB 文件全部失败 | `embed_texts` **分批** + **自适应上限**：从端点报错解析真实上限并缓存到类属性（实测同一端点两次分别声称 20 与 10，因此写死常量必然再次失效）。修复后 200KB → 201（9.4s）、800KB → 201（37.6s） |
| **D60** | **孤儿向量检测用相似度搜索"枚举"全库** | 用一次 top-K 相似度搜索获取"Milvus 里有哪些 id"，而它只返回离查询向量最近的 K 条 → 每次只看到 500 个且**每次不同**（"每清理一次只删 1–2 个"，493 → 492 → 490）。更糟：没落进这 K 条的孤儿**永远查不出来**，检测本身不可靠 | 新增 `MilvusVectorStore.list_all_ids()`（`query` + 过滤表达式）做真正的枚举 —— 一次看清 532 个向量、519 个孤儿、一次清理干净。`check_embedded_consistency` 的类型 A 有同样问题且会产生**假阳性**（把存在的 chunk 误报为幻影），已一并修正；`delete` 改为分批 |

D9 的教训最为关键：**评估框架自身会用"看起来更小"的指标掩盖失败**，
这与 §15"不得悄悄忽略失败 case"是同一条原则。
D9 修复后重测，直接推翻了基于脏数据得出的稳定性结论（见 §8A.5 注释）。

### 8A.8 线上与离线的评分口径差异（ADR-021）

评测的可复现性与线上答题的延迟是**两个不同的需求**，因此采样数分开配置：

| 路径 | 配置项 | 采样数 | 每轮 LLM 调用 | 实测耗时/轮 |
| --- | --- | --- | --- | --- |
| 线上答题 | `settings.online_analysis_samples` | **1** | 1 分析 + 1 追问 = **2** | 约 9.5s |
| 离线评估 | `settings.offline_analysis_samples` | **3** | 3 分析 + 1 追问 = **4** | 约 27–38s |

代码中的落实：

- `answer_analyzer.online_analysis_samples()` / `offline_analysis_samples()`
- `evaluator.online_evaluation_samples()` / `offline_evaluation_samples()`
- 线上路径（`interview_turn_service`、`evaluation_service`）
  显式传线上采样数；离线路径（`evaluation/runner`、`evaluation/stability`）
  显式传离线采样数。
- **两处都不依赖函数默认值**，避免某天改默认值把另一条路径悄悄带偏。

> ⚠️ **必须记住的后果：线上分数比评估分数波动更大。**
>
> 单次采样在档位边界会跳档，实测同一输入极差可达 20–30 分；
> 3 次采样合并后降到 0–25 分（多数轮次为 0）。因此：
>
> 1. **不要把线上分数与评估报告里的分数直接比较。**
>    例如评估里 senior 回答是 `90/90/90`，线上可能出现 `90/70/70`。
> 2. 两个数字**都对**，只是分属不同测量口径。
> 3. 线上若需要更稳的分数，应提高 `online_analysis_samples`
>    并接受相应延迟，而不是去改评测阈值。
>
> 报告已写入两套采样数（`run_config.sampling` 与稳定性报告的
> `config.sampling`），使**报告能自证用的是哪套口径**。
> 这修复了一次真实事故：曾把 D11 修复前的报告当成 samples=3 的基线，
> 因为当时的报告无法区分自己用的是几次采样（D16）。

### 8A.9 当前缺口

- 🟡 **评分一致性显著改善，但"基本稳定"是被夸大的说法（D43）**。
  锚点化的对照效果是真实的（§8A.5B：平均极差 10.7 → 4.4、
  完全稳定的维度 1/18 → 15/18）。**但 `--repeats 10` 的实际
  运行结果没有"5/6 轮完全一致"那么好。**

  8 次**覆盖率 100%** 的全量运行（排除存在样本缺口的那次，
  见 §22.1）：

  | 稳定轮次 | 3/6 | 3/6 | 3/6 | 3/6 | 2/6 | 5/6 | 3/6 | 4/6 |
  | --- | --- | --- | --- | --- | --- | --- | --- | --- |
  | 平均极差 | 15.0 | 13.3 | 12.5 | 14.2 | 15.8 | 4.2 | 12.5 | 7.5 |

  - 稳定轮次中位数 **3 / 6**，均值 3.5 / 6
  - 平均极差中位数约 **13**

  ⚠️ 原文此处写"实测 5/6 轮的三项分数在 10 次重复中完全一致，
  平均极差从 10.7 降到约 1.4" —— 那**是 8 次里最好的一次**，
  而且当时那次还有样本缺口。把它写成普遍结论是选择性引用。

  **正确表述**：锚点化把平均极差从 10.7 降到约 13 分/轮，
  但**典型的 6 轮里有 3 轮仍会超出 15 分阈值**。
  残余波动集中在 `answer_quality` 的 B(70) / C(40) 档位边界，
  对策是收紧锚点描述，而不是放松阈值（§22.1）。

  **原"阻塞 Phase 6"的判断仍然解除** —— 因为 §23.5 的触发条件
  本就写的是"全部 ≤ 10 分"，而这里记录的是当前真实水平与差距，
  不是用它当门禁（ADR-015：评分波动不可作为质量门禁）。
- 🟡 **`missing_points` 条数可控，但词面去重有明确上限**（D11 / D18 / D19）：
  当前实现为"字符 bigram + Dice 聚类 + 包含抑制"，再由 `max_items=6` 截断，
  条数不再随采样数膨胀。
  但实测 18 条（真实主题 6 个）时，相似度阈值低到 0.30 也只能降到 8 条。
  要根治需引入 embedding 做语义聚类 —— 新的架构决定，需先评估成本与收益。
- 🟢 **`strengths` / `weaknesses` / `suggestions` 已持久化**（D20）：
  迁移 `b41f7c9a2e38` 新增四个 JSON 列，Phase 6 的能力画像与训练建议
  现在有数据可用。已验证 upgrade / downgrade 往返与端到端写入。
- 🟢 **历史评分可事后解释**（D21）：`scoring_details` 记录
  锚点、`anchor_reason`、采样次数、每次采样分数与口径快照。
  这解决了 ADR-021 引入的"线上与离线分数不同但无法追溯来源"问题。
- 🟡 **`scoring_details` 的存储成本未评估**：
  每条评价约多存 0.5–2KB。当前数据量下可忽略，
  但若评价量增长到百万级需重新评估。
- 🟡 **采样成本**：`DEFAULT_SAMPLES = 3` 使每轮分析的 LLM 调用变为 3 倍，
  加 1 次追问共 **4 次调用/轮**（实测每轮耗时约 27–38s）。
  曾实验提到 5 次，被对照实验否定（见 ADR-020）——**不要仅凭直觉调大采样数**，
  任何调整都要附同 case、同 repeats 的对照数据。
- 🟡 **阈值保持 15，不得为了通过而放宽**：
  实测残余极差仍由"双峰跳档"造成（25 分）。
  把全局阈值抬到 25 会让系统变成"永远通过"，
  这与 §8A.7 D9 属于同一类错误——为了让指标好看而放宽判据。
- 🟡 **样本量仍偏小**：4 个 case / 6 轮，`R1` 有 3 个有效样本、`R3` 有 3 个。
  相比最初的 1 个已是改善，但**不足以支撑趋势判断**。
- 🟡 **无逐题评价**：R4 只校验整场三项总分，不含逐题 rubric。
- 🟡 **整场评价已锚点化（ADR-016 / D12）**：`evaluator.py` 现在也是
  锚点制 + 3 次采样合并，实测 3 轮四项分数极差为 0。
  **逐轮与整场的评分口径已统一。**
- 🟢 **`strengths` / `weaknesses` / `suggestions` 已持久化**（D20，迁移 `b41f7c9a2e38`）：
  这三个字段此前只存在于 LLM 返回值中，现在已落库，
  Phase 6 "能力画像与训练建议" 有数据可用。
- 🟡 **`missing_points` 词面去重的能力上限**（D19 / ADR-019）：
  实测 18 条（真实主题 6 个）时，相似度阈值低到 0.30 也只能降到 8 条，
  现行配置降到 8 条后由 `max_items=6` 截断。
  要根治需引入 embedding 做语义聚类（新的架构决定）。
- 🟡 **Spearman 相关系数在退化序列上无定义**：
  锚点化后同一维度多次采样常取同一档位，属于常数序列，
  因此稳定性报告里该系数**大多显示"无定义"**，这是正确行为而非缺陷。
- 🔴 **不得**用评分均值衡量模型升级效果（见 ADR-015 与 §8A.5 结论 4）。

### 8A.10 下一步（按优先级）

1. **降低档位边界造成的残余跳档** —— 已通过 ADR-017（多次采样合并）大幅缓解。
   若后续仍观察到跳档，可考虑为 B / C 边界补充**可判定的计数条件**
   （例如"至少两处具体信息"），把主观判断变成可核对的条件。
   已否决的方案：增加档位（见 ADR-016 的"已被否决的方案"）。
2. **语义去重 `missing_points`**（可选，需架构决定）：
   当前词面去重只能把 18 条压到 8 条，达不到真实主题数（6）。
   要做到语义去重需引入 embedding 做向量聚类，
   属于新的外部依赖，需先评估成本与收益。
3. **继续扩数据集**：补 jd / project / code 方向的面试 case，
   使 `R1` / `R3` 的样本量足够支撑趋势判断。
4. ~~**补齐 `evidence_chunk_ids`**~~ ✅ 已完成（迁移 `c7d2e5f8a1b4`，§5.4）。
5. ~~**实现面试状态机（§10）**~~ ✅ 已完成：基础设施 + 业务动作接入（ADR-025）。
6. ~~**把业务动作接入状态机**~~ ✅ 已完成（§10.3）；
   转移轨迹也已实现（§10.7）。
7. **补齐 `InterviewSession.user_id`**：✅ 已完成（ADR-031）。

---

## 9. Interview Engine 与内部 Agent 设计

### 9.1 目标

Interview Engine 不是“多个 Agent 自由对话”，而是一条受状态机、检索证据和题目计划约束的面试流程。

### 9.2 内部职责与当前实现

| 内部角色 | 输入 | 输出 | 约束 | 状态 |
| --- | --- | --- | --- | --- |
| Interview Planner | JD、简历、项目目标、检索摘要 | 面试轮次与主题计划 | 计划须可保存、可复现 | 🔵 未实现 |
| Question Generator | 当前主题、资料证据、历史问题 | 一道题及证据 ID | 不重复；不把推测说成事实 | ✅ `question_generator.py` + `context_builder.py`，**已返回并落库 evidence_chunk_ids**；"不重复"仍未实现 |
| Follow-up Controller | 回答、评价、计划 | 追问或进入下一题 | 有预算与停止条件 | 🟡 `follow_up_generator.py`，**无预算与停止条件** |
| Answer Evaluator | 回答、题目、资料证据、评分 rubric | 评分、反馈、证据与缺口 | 区分材料事实与回答表现 | 🟡 `answer_analyzer.py`（逐轮）+ `evaluator.py`（整场），**均已锚点化**；两者均**无证据引用** |
| Summary Generator | 整轮会话记录 | 优势、风险、训练建议 | 可追溯至回答与证据 | 🟡 整场 `feedback` 已实现，**无独立总结与追溯** |

实现位置：

```text
app/services/interview/
├── context_builder.py           检索上下文组装
├── question_generator.py        Question Generator
├── answer_analyzer.py           Answer Evaluator（逐轮）
├── follow_up_generator.py       Follow-up Controller
├── evaluator.py                 Answer Evaluator（整场）
├── interview_engine_service.py  生成并保存问题
├── interview_turn_service.py    一轮完整流程（回答 → 分析 → 追问）
└── evaluation_service.py        整场评价与落库
```

> ⚠️ 上表中的“尚无 / 无”是当前真实缺口，不是待办承诺。
> 其中 **`evidence_chunk_ids` 与显式 rubric 是进入 Phase 6 前必须补齐的两项**，
> 否则 §9.3 的提问原则与 §7.5 的原则 3 无法成立。

> ⚠️ 当前存在分层不统一问题：`interview_session_service.py`、`interview_question_service.py`、
> `interview_answer_service.py` 位于 `app/services/` 根目录，而其余面试服务在
> `app/services/interview/`。这是历史遗留，尚未统一；移动会牵动导入路径，需单独一次重构。

最初可以由一个受控的服务实现这些职责；只有当提示词、状态和评估需求稳定后，才考虑将其拆分为内部模块。不得为了“多 Agent”而增加通信与状态复杂度。

**当前判断：** 提示词、状态与评估需求**尚未稳定**（见 §8A.5 的稳定性实测：同一输入重复 10 次，
评分极差达 10–20 分），因此**不进行** Planner / Summary 的进一步拆分。

### 9.2.1 检索当前是**纯稠密单路**（记录事实，避免重复排查）

`RetrievalService.retrieve` 全文只做四步：嵌入 query → Milvus 按
**COSINE** 搜 top-k（默认 5）→ 按 id 回表取正文 → 返回。
`score` 直接是 Milvus 的 `distance`，**没有任何加工**。

因此以下机制**都不存在**，不要在讨论中假定它们已经存在：

| 机制 | 状态 |
| --- | --- |
| 稀疏/BM25 召回 | 不存在 |
| 稠密+稀疏的权重（λ 之类）| 不存在 |
| **两路分数归一化**（Min-Max / Z-Score）| 不存在 —— 因为没有两路 |
| query 改写、rerank、相似度阈值过滤 | 不存在 |
| query 类型分类（判断"模糊输入"等）| 不存在 |

（`make_test_fixtures.py` 与 `test_document_parsing.py` 里出现的
"BM25 混合检索"是**被解析的测试素材文本**，不是实现。）

**为什么记这一条**：这些概念在 RAG 语境下太常见，容易被当作
"显然已经做了"。而一旦基于错误假设讨论调参，排查方向从一开始就是错的。

**若将来要做**：先补检索基线（Recall@1/3/5、MRR —— 框架在 Phase 4
已建好），否则无法判断改动是变好还是变坏。

### 9.3 提问原则

- 资料型问题必须包含可追溯 `evidence_chunk_ids`。
- 通用能力题要显式标记为 `general`，不伪装成资料事实。
- 追问应针对回答中模糊、矛盾、缺失量化指标或与 JD 不匹配之处。
- 每轮题目预算、主题覆盖与终止条件必须由 `InterviewSession` 持久化。

---

## 10. 面试状态机

状态：✅ **基础设施与业务接入均已完成**。

实现位置：`app/services/interview/interview_state_machine.py`。

### 10.1 状态与转移（已实现）

```text
draft
  → preparing_context
  → planned
  → asking
  → waiting_for_answer
  → evaluating
  ├─ asking（追问或下一题）
  └─ summarizing → completed

任何可恢复状态 ─→ paused
任何未完成状态 ─→ cancelled / failed
```

| 状态 | 允许的后继状态 | 实现 |
| --- | --- | --- |
| `draft` | `preparing_context`、`cancelled` | ✅ |
| `preparing_context` | `planned`、`failed`、`cancelled`、`paused` | ✅ |
| `planned` | `asking`、`paused`、`cancelled` | ✅ |
| `asking` | `waiting_for_answer`、`paused`、`failed` | ✅ |
| `waiting_for_answer` | `evaluating`、`paused`、`cancelled` | ✅ |
| `evaluating` | `asking`、`summarizing`、`paused`、`failed` | ✅ |
| `summarizing` | `completed`、`failed` | ✅ |
| `paused` | `preparing_context`、`planned`、`asking`、`waiting_for_answer`、`cancelled` | ✅ |
| `completed` / `cancelled` / `failed` | 无（终态） | ✅ |

设计说明：

- `evaluating` 的后继写 `asking`，而不是分别列出 `follow_up` / `next_question`。
  那两个是**行为分支**而非独立状态，落库时都表现为"回到 asking"，
  这样状态数保持可控，分支决策留在业务层。
- 非法转移抛出 `InvalidTransitionError`，Router 转成 **409**，
  不静默忽略 —— 静默失败会让调用方误以为状态已改。
- 未知状态值**直接报错**，不做静默兜底。
  状态写错会让会话卡在无法解释的状态里，必须在写入时暴露。

### 10.2 旧状态兼容

迁移前会话的状态是 `"created"`，语义等于 `draft`。
`LEGACY_STATUS_MAP` 在**读取时**兼容它，**不做数据回填** ——
回填会改写历史记录，而历史状态本身是审计信息的一部分。

已在真实数据上验证：库中 2 条 `created` 记录被正确解读为 `draft`，
且允许的转移与 `draft` 一致。

### 10.3 已接入的范围

- ✅ 新建会话初始状态为 `draft`
- ✅ `InterviewSessionService.transition_status()` / `apply_transition()` 带校验
- ✅ `POST /api/interviews/{id}/transition`（非法转移 409）
- ✅ `GET /api/interviews/{id}/allowed-transitions`
- ✅ 状态校验的完整单元验证（26 条合法转移、穷举 121 个状态组合、终态、旧值兼容）
- ✅ **业务动作已接入状态推进**（ADR-025）：
  - `InterviewEngineService`：生成问题成功 → `planned`/`asking` → `asking`
  - `InterviewTurnService`：答题成功 → `asking → waiting_for_answer → evaluating → asking`
  - `InterviewEvaluationService`：评价落库成功 → `evaluating → summarizing → completed`
- ✅ 端到端流程验证：`app/core/test_interview_flow.py`（20 项断言）

### 10.4 状态推进的规则（ADR-025）

**核心规则：只在外层调用成功后才推进状态。**

具体实现是"先在内存中标状态、最后一次性提交"，而不是每步都提交：

```text
校验状态（不写库）
  → 保存回答
  → 调 LLM 分析          ← 可能失败
  → 调 LLM 生成追问       ← 可能失败
  → 保存追问
  → 走完状态链条并提交     ← 只有到这里才落库
```

为什么不能"先声明意图再执行"：
那看起来更符合状态机的直觉，但会让会话**卡在 `evaluating`**。
LLM 失败后状态已提交为 `evaluating`，而它的语义是"正在评价"，
用户既不能继续答题，也无法解释为什么。

失败后的行为：

| 失败位置 | 会话停在 | 可否重试 |
| --- | --- | --- |
| 状态校验不通过 | 原状态（未写任何数据） | 需先修正状态 |
| 保存回答失败 | `asking` | ✅ 直接重试 |
| LLM 分析 / 追问失败 | `asking` | ✅ 直接重试 |
| 评价调用失败 | `evaluating` | ✅ 再次调用评价即可 |

`evaluating` 可重试是关键：`evaluating → summarizing` 本身合法，
因此**不需要**为"重评"新增任何转移规则。

### 10.5 completed 是真终态（ADR-026）

`completed` 没有任何后继状态，**评完不可重评**。理由：

1. **项目定位把面试当一次性事件。** §1.2 是"完成一轮模拟面试"，
   "多次面试历史、能力趋势"按 §19 排在 **V1**，不属于 MVP。
2. **重跑同一场面试语义不自洽。** 题目与回答都固定，重跑唯一变的是
   LLM 采样，即**测量噪声**而非新信息。要重练应开**新会话** ——
   还能天然获得"两次面试对比"，而重评旧会话会堵死这个能力。
3. **评分本身不可复现。** ADR-020/021 已证明同一输入在 3 次采样下
   有 0–25 分极差。允许重评等于让用户看到"同一场面试两次不同分数"，
   而那并不是面试表现的变化。
4. **代码已经是这个方向。** `InterviewEvaluationRepository.create`
   每次 `INSERT` 新行、`get_by_session_id` 取最新，
   库里本就能存多条评价；真正该做的是**系统级重评**
   （换模型/改 Prompt 后重算历史），那属于离线评估的职责。

**代价（必须明确记录，避免以后当成 bug）**：
评估一旦完成就不可重评，即使用户认为评错了。
MVP 可接受；若将来要支持，需要先支持"多版本评价记录 + 显式选择版本"，
而不是简单放宽转移表。

### 10.6 尚未实现

- 🔵 **`paused` 的恢复上下文快照**。恢复时需要知道停在哪一步。
- 🔵 **`Interview Planner` 与计划快照填充**。
  `plan_snapshot` 列已就绪但恒为空。

### 10.7 状态转移轨迹（已实现）

会话表只保存**当前**状态。一旦状态变了，就没有任何记录能回答
"这场面试什么时候从 asking 变成 evaluating"、"为什么停在这里"。
`interview_status_history` 承担这个职责
（ADR-034，迁移 `2669a0dbeaa6`）。

一轮答题留下的完整轨迹：

```text
draft              → preparing_context  [interview_started]
preparing_context  → planned            [interview_started]
planned            → asking             [question_generated]
asking             → waiting_for_answer [answer_submitted]
waiting_for_answer → evaluating         [analysis_completed]
evaluating         → asking             [follow_up_generated]
asking             → paused             [user_paused]
paused             → asking             [user_resumed]
asking             → waiting_for_answer [user_finished]
waiting_for_answer → evaluating         [user_finished]
evaluating         → summarizing        [evaluation_started]
summarizing        → completed          [evaluation_completed]
```

三条硬约束：

1. **与状态变更同一次事务提交。** 写入侧只 `add` 不 `commit`，
   失败时随事务回滚。否则轨迹里会出现"从未发生"的转移 ——
   那比没有轨迹更糟，因为它会把人引向错误的方向。
2. **`trigger` 必须说实话。** 预算耗尽时走的是同一个
   `finish_interview`，但不能记成 `user_finished`：
   轨迹写 `budget_exhausted`，否则复盘时会以为是用户点的结束。
3. **直接改写 `status` 的调用点必须调 `record_status_change` 补记。**
   生成题目时是把 `planned` 直接置为 `asking`（那是 planned
   唯一合法的常规后继），这种"直接写"若不补记就会留下轨迹缺口。

查询接口：`GET /api/interviews/{id}/history`（正序）。
`from_status` 原样返回库里的值（含历史 `"created"`），不做规范化 ——
轨迹是审计记录，"当时库里是什么"必须可考。

---

## 11. API 分层与演进

### 11.1 当前 API

| 资源 | 已有端点 | 状态 |
| --- | --- | --- |
| 系统 | `GET /`、`GET /api/health` | ✅ |
| 认证 | `POST /api/auth/register`、`POST /api/auth/login`、`GET /api/auth/me` | ✅ |
| 项目 | `POST/GET /api/projects`、`GET/PUT/DELETE /api/projects/{project_id}` | ✅ |
| 文档 | `POST /api/projects/{project_id}/documents`、`GET /api/projects/{project_id}/documents`、`POST /api/projects/{project_id}/documents/{id}/embed`、`DELETE /api/projects/{project_id}/documents/{id}` | ✅ |
| 面试——会话 | `GET /api/interviews`（**历史列表**，支持 `unfinished_only` / `project_id` / 分页）、`POST /api/interviews`、`GET /api/interviews/{id}`、`GET /api/interviews/{id}/detail` | ✅ |
| 面试——开始 | `POST /api/interviews/{id}/start` | ✅ |
| 面试——状态 | `GET /api/interviews/{id}/allowed-transitions`、`POST /api/interviews/{id}/transition`、`POST /api/interviews/{id}/pause`、`POST /api/interviews/{id}/resume` | ✅ |
| 面试——轨迹 | `GET /api/interviews/{id}/history` | ✅ |
| 面试——答题 | `GET /api/interviews/{id}/questions`、`GET /api/interviews/{id}/questions/{qid}/evidence`（**资料依据片段**）、`POST /api/interviews/{id}/answer` | ✅ |
| 面试——结束 | `POST /api/interviews/{id}/finish` | ✅ |
| 面试——能力画像 | `GET /api/interviews/profile/ability`（Phase 6，跨场次聚合）| ✅ |
| 用量 | `GET /api/usage` | ✅ |
面试 API 的设计约定：

1. **`start` 是一个动作，不是三个状态转移。**
   `draft` → `preparing_context` → `planned` →（生成首题）→ `asking`
   在服务端一次完成。暴露三个转移会让前端必须自己实现状态机规则，
   并在中途失败时留下半开状态（停在 planned 但没题目）。
   已暂停在开始前状态的会话会先自动恢复。
2. **暂停/恢复是专门端点，不复用 `transition`。**
   暂停必须同时写入 `resume_status`，否则会话会变成只能取消的死胡同；
   让调用方自己去拼这个组合太容易出错。
3. **`allowed-transitions` 对 paused 会话动态计算**，
   前端不必重复实现状态机规则，避免前后端规则逐渐不一致。
4. **`question_index` 由服务端决定，不接受调用方传入。**
   原先由脚本调用时靠人工约定（首题传 0 还是 1），接口化之后
   必须统一，否则索引会错乱。索引与状态在同一次提交里前进。
5. **`answer` 必须校验 `question_id` 属于路径上的会话。**
   所有权校验在会话上，但题目与会话的从属关系必须单独确认 ——
   只信任请求体里的 `question_id` 会让用户对别人的题目作答。
6. **`answer` 的响应只回档位与缺口数量，不回完整分析。**
   面试过程中把"技术深度偏低"直接展示给候选人会干扰后续作答
   （他还没答完）；完整分析留到 `finish` 后的评价里。
7. **`finish` 内部把会话搬到 `evaluating` 再触发评价**，
   这属于流程编排（见 `InterviewFlowService`），
   不塞进会话管理或评价服务，避免那两侧承担额外职责。
8. **配额在 `start` 消费，返回 429 而不是 403。**
   429 的语义是"限额用尽、稍后可重试"，与"无权访问"完全不同；
   前端据此显示"本月额度已用完"而不是"你没有权限"。
   被拒时会回滚计数，用户不会看到负数余额（ADR-032/033）。

### 11.2 后续 API 边界

| API 域 | 计划职责 | 状态 |
| --- | --- | --- |
| Documents | 文档列表、详情、重处理、删除、索引状态 | 🔵 |
| Retrieval | 受鉴权保护的调试检索或仅内部调用 | 🔵 |
| Evaluation | 启动评估、读取报告；仅开发/管理员环境 | 🔵 |
| Interviews | 创建、开始、答题、暂停、恢复、结束、复盘 | 🔵 |
| Feedback | 历史表现、能力维度、练习建议 | 🔵 |

API 不直接暴露其他用户的 `chunk_id`、全文或向量。每个与项目相关的端点必须从当前用户身份验证项目所有权。

---

## 12. 性能规划

### 12.1 近期基线

- 🟡 在 Evaluation Runner 中采集：Query Embedding、Milvus 搜索、PostgreSQL 回填与总耗时。
- 🟡 固定默认 `limit` 和评估数据集版本，才能比较优化前后表现。

### 12.2 后续优化路线

| 项目 | 方向 | 状态 |
| --- | --- | --- |
| Chunk 创建 N+1 | 移除逐条 refresh，改为批量返回或按需查询 | 🔵 Performance Refactor |
| 文档索引 | 将长文档 Embedding 迁出 HTTP 请求，使用可重试后台任务 | 🔵 |
| Embedding | 保留批量调用；按供应商限制实施分批与退避 | 🔵 |
| Retrieval | 合理 Top-K、元数据过滤、必要时 rerank | 🔵 |
| Redis | 缓存短期重复查询、会话状态、限流计数 | 🔵 |
| Milvus | 监控索引、分区策略与容量；只在数据规模证明必要时优化 | 🔵 |
| 大文件 | 流式上传、文件大小限制、异步解析与任务进度 | 🔵 |

优化必须以 Evaluation 指标、端到端时延和资源成本为依据；不得为猜测的性能问题提前破坏当前清晰的抽象。

---

## 13. 一致性、重试与数据生命周期

### 13.1 当前一致性策略

1. PostgreSQL 的 `DocumentChunk` 是权威业务记录；Milvus 是可重建索引。
2. Pipeline 仅选择 `embedding_status = pending` 的 Chunk。
3. 先成功写入 Milvus，再将 Chunk 标记为 `embedded`。
4. Milvus 使用 `chunk.id` 为主键并使用 `upsert`：若向量已写入但 DB 状态提交失败，重试可安全覆盖同一向量。

### 13.2 已知边界与后续方案

PostgreSQL 与 Milvus 之间没有分布式事务，因而无法提供严格原子提交。当前策略是可恢复的最终一致性，而不是伪造“两边同时提交”。

- 🟡 需要明确失败日志与重试入口。
- 🔵 后续引入索引任务表 / outbox、指数退避、死信状态和定期 reconciliation。
- 🔵 文档或 Chunk 删除时，同时删除对应 Milvus 向量；处理失败时可由 reconciliation 补偿。
- 🔵 文档重新切块时，旧向量必须显式失效，避免孤儿搜索结果。

---

## 14. 权限与安全

### 14.1 当前措施

- ✅ 密码以 hash 保存。
- ✅ JWT Bearer Token 识别当前用户。
- ✅ 项目查询、更新、删除通过 `owner_id` 进行所有权检查。
- ✅ 文档上传前验证项目属于当前用户。
- ✅ Embedding API Key、JWT Secret 等配置由环境变量承载。

### 14.2 后续安全清单

- 🔵 文件大小、扩展名、MIME、编码与恶意内容校验；避免只信任客户端文件名。
- 🔵 上传路径安全、对象存储访问控制和下载授权。
- 🔵 检索服务与未来面试 API 中实施同样的项目所有权校验，不能只信任传入的 `project_id`。
- 🔵 速率限制、登录保护、审计日志、密钥轮换与依赖漏洞检查。
- 🔵 隐私政策、用户数据导出 / 删除、资料保留策略。
- 🔴 不以模型输出为可信指令执行系统操作；面试材料是非可信输入，必须防范 Prompt Injection。

---

## 15. 测试策略

| 层级 | 目标 | 当前 / 后续状态 |
| --- | --- | --- |
| 单元测试 | Chunking、过滤表达式、服务边界、状态转换、评估判定规则 | 🟡 `judges.py` 与 `model_json.py` 已有内置自测；其余仍是脚本，需标准化为测试套件 |
| 集成测试 | PostgreSQL、Qwen、Milvus 真实链路 | ✅ 已有 Embedding / Milvus / Retrieval 验证脚本 |
| RAG Evaluation | 离线召回质量与过滤正确性 | ✅ Runner 已实现，5 份基线报告（见 §8.4） |
| Interview Evaluation | 面试引擎的缺口识别、追问质量与评分稳定性 | 🟡 框架已可用（见 §8A），样本量不足，词面去重有上限 |
| 面试流程 E2E | 状态机推进、资料依据、落库一致性 | ✅ `app/core/test_interview_flow.py`（20 项） |
| 面试 API | 路由、认证与所有权、请求校验、状态码、暂停/恢复 | ✅ `app/core/test_interview_api.py`（49 项） |
| API 测试 | JWT、项目隔离、上传、错误响应 | 🟡 面试域已覆盖；项目与文档域仍待补 |
| 回归测试 | 每次调整 chunk、embedding、索引或提示词后防退化 | 🟡 RAG 有基线可比；面试侧因评分波动过大（§8A.5），暂不可作为门禁 |

### 15.1 当前关键验证脚本

**RAG / 索引链路**

- `app/core/test_embedding.py`：Embedding 基础验证。
- `app/core/test_milvus.py`：Qwen → Milvus 基础写入与搜索验证。
- `app/core/test_embedding_pipeline.py`：自动选择 pending Chunk，验证 Qwen → Milvus → DB `embedded` → 搜索可见性。
- `app/core/test_retrieval.py` / `test_retrieval_filter.py`：检索与过滤验证。
- `app/core/cleanup_test_milvus.py`：清理临时测试向量 `999999`、`1000000`。

**面试链路**

- `app/core/test_create_interview_session.py`：创建干净的测试会话。
- `app/core/test_interview_flow.py`：**端到端流程验证**（20 项断言）——
  状态机在业务链路中的推进、非法转移被拒且不改库、
  资料依据捕获与继承、`completed` 终态、被拒操作不写脏数据。
  需要 PostgreSQL + Milvus + LLM，**不参与评分统计**。
- `app/core/test_interview_api.py`：**API 层验证**（49 项断言）——
  用真实 JWT 通过 ASGI 直接调用 HTTP 端点，
  覆盖认证与所有权（401/404）、请求校验（422）、
  非法转移（409）、暂停/恢复全链路、从 `draft` 与从 `planned`
  两条开始路径、答题与追问、结束与评价、终态拒绝作答。
  这一层测的是 service 层测不到的东西：路由、序列化、状态码。
  需要 PostgreSQL + Milvus + LLM，**不参与评分统计**。
- `app/core/test_status_history.py`：**状态轨迹验证**（30 项断言）——
  触发原因枚举覆盖、失败的转移不留轨迹、`/start` 的两个中间步、
  一轮答题的三个中间态逐步可见、暂停/恢复、评价完成、
  轨迹无自转移且首尾相接、删除会话时级联删除。
  需要 PostgreSQL + Milvus + LLM，**不参与评分统计**。
- `app/core/test_question_quota.py`：**按题计量验证**（26 项断言）——  两份额度的纯逻辑与受限方判定、
  **核心漏洞用例**（单场预算 30 + 月度额度 3 → 第 4 题被 429 挡住）、
  追问计入额度、额度耗尽后新会话也无法开始、被拒后用量不上涨。
  **不需要 Milvus**，只需 PostgreSQL + LLM。
- `app/core/test_support.py`：**验证脚本的共享脚手架** ——
  `grant_quota` / `reset_quota` / `get_used` / `delete_sessions`。
  凡依赖特定库状态的脚本，自己 prepare、自己 restore（见 D38/D39）。
  注意 `grant_quota` **同时设置两份额度**：只设场次会让
  "题目额度先耗尽"悄悄挡住测试。
- `app/core/test_usage_quota.py`：**用量与配额验证**（26 项断言）——
  决策器与账期纯逻辑、账期补零、零额度语义、
  创建草稿不扣额度、开始面试扣额度、配额耗尽返回 429、
  被拒后用量回滚且会话仍为 draft、只读查询不改变用量、
  所有者不变式。
  **不需要 Milvus**，只调一次 LLM，因此比其它脚本快得多。
  运行前会清空本账期用量并把配额设为测试值，结束时恢复 ——
  否则前一次运行的累计消耗会让它失败（见 D38）。
- `app/core/test_question_budget.py`：**题目预算与终止条件验证**
  （31 项断言）—— 决策器纯逻辑（含越界夹取）、预算内会生成追问、
  预算耗尽时自动结束并带回评价、终止原因持久化、结束后拒绝作答、
  协议边界（超限 422 / 最大值 / 默认值）、
  轨迹记录 `budget_exhausted` 而非 `user_finished`。
  需要 PostgreSQL + Milvus + LLM，**不参与评分统计**。
- `app/core/test_interview_engine.py`：完整链路（生成问题 → 回答 → 分析 → 追问 → 整场评价）。
- `app/core/test_interview_turn.py`：单轮 Turn 链路。
- `app/core/test_answer_analyzer.py` / `test_follow_up_generator.py`：分析器与追问生成器。
- `app/core/test_evaluator.py` / `test_evaluation_service.py`：整场评价与落库。
- `app/core/test_interview_session_repository.py`：会话问题与回答联合查询。

**评估框架（可重复运行、带自测）**

- `python -m app.evaluation.runner`：RAG 检索评估。
- `python -m app.evaluation.interview.judges`：判定规则自测 + 数据集一致性检查（不调 LLM）。
- `python -m app.evaluation.interview.cli`：面试引擎质量评估（约 5 分钟）。
- `python -m app.evaluation.interview.cli --stability --repeats 10`：
  评分稳定性基线（约 30 分钟）。**不再是换模型的必跑项**，
  只在怀疑有系统性问题时才跑（ADR-039）。
- `python -m app.evaluation.interview.report_compare`：
  **对比两份评测报告**（纯读 JSON，几秒，不调 LLM）。
  换模型的标准动作，见 §26。
- `python -m app.evaluation.interview.report_compare --self-test`：
  对比工具自测（13 项，不读真实报告）。

**资料与向量（需要 Docker + Milvus）**

- `python -m app.core.make_test_fixtures`：生成解析测试样本
  （`app/core/fixtures/`）。**测试不该依赖"碰巧留在磁盘上的文件"** ——
  曾经因为把用户的真实简历当样本、清理后测试就崩了。
- `python -m app.core.test_document_parsing`：解析与清洗规则（16 项，不碰数据库）。
  关键断言是**中文去空格与英文保空格这对矛盾**。
- `python -m app.core.test_resume_upload`：上传全链路（14 项）——
  可选文本 PDF 能上传并索引、图片型 DOCX 被明确拒绝、
  **失败后不留记录也不留文件**。
- `python -m app.core.test_paused_finish`：**暂停会话能否结束**（10 项，需数据库）。
  覆盖：有/无 `resume_status`、有未答题/已答过、无任何题目、
  以及**重复结束返回 409**。这是 D56 的回归防护。
- `python -m app.core.test_interview_list`：**面试历史列表**（21 项）——
  结构、按更新时间倒序、`unfinished_only` 过滤、项目过滤、分页、
  `answered_count` 与 `questions_asked` 的区别、所有权隔离、参数校验。
- `python -m app.core.test_interview_evidence`：**资料依据片段**（16 项）——
  片段内容与来源、**顺序与 `evidence_chunk_ids` 一致**、截断标记、
  资料被删除时明确报出失效依据、以及越权防护
  （他人的会话 / 别的会话的 question_id 都返回 404）。
- `python -m app.core.test_llm_error_mapping`：**上游 LLM 失败的映射**（8 项，D57）。
  需要额外起一个指向不可达 LLM 的后端（见该文件头部说明），
  断言 LLM 失败返回 503 且业务异常（409/404/401）不被误判。
- `python -m app.core.test_chunk_guard`：**切块守卫**（4 项）。
  需要另起一个 `upload_with_broken_chunker`（把 `split_text`
  打成永远返回空），断言"有内容却切不出块"返回 400 且
  **不留下静默死档**。
- `python -m app.core.test_doctor`：**依赖健康检查自身**（4 项）。
  断言依赖正常时不误报、四项外部依赖不可达时**全部**报 FAIL、
  恢复配置后不残留假故障 —— 一个永远报 OK 的检查比没有更糟。
- `python -m app.core.test_async_indexing`：**异步文档索引**（16 项）——
  上传 202 且立即返回、入队幂等、worker 推进状态、
  **并发领取零重复**、租约恢复（含"未过期不被抢走"）、
  重试上限、无 chunk 不排队。
- `python -m app.core.test_embedding_batching`：**嵌入分批与自适应上限**（16 项）。
  用**打桩客户端**（不依赖真实端点）验证：上限解析、
  大批量自动分批、**顺序未错位**、初始猜测过大时从报错自学、
  与批量无关的 400 原样抛出。
- `python -m app.core.test_logging`：**请求上下文与日志**（11 项）。
  `X-Request-ID` 存在且每请求不同、上游 ID 被沿用、
  404/401 也带 ID、日志行含 request_id / user / session。
- `python -m app.core.test_ability_profile`：**能力画像**（21 项）——
  中位数 / 区间 / 极差、逐次趋势**按时间正序**、重复弱点识别、
  样本不足时不下结论、无数据的项目返回空画像、未认证 401。
- `python -m app.core.cleanup_legacy_evaluations [--purge]`：
  检查（默认）/ 清理"三组结构化内容全空"的评价。
  **默认只检查** —— 那些评价的 `feedback` 是真实内容，
  删掉不可逆；画像已通过过滤排除它们，通常不需要删。
- `python -m app.core.repair_missing_chunks [--repair]`：
  回填"有内容但没有文本块"的文档（历史数据，§27.10）。
  切块参数与上传流程严格一致，避免回填的文档与正常上传的
  文档在检索表现上不同。
- `python -m app.core.cleanup_demo_sessions [--dry-run] [--keep 1,2]`：
  清理测试留下的面试会话。**默认保留会话 1、2**，
  且按 `user_id` 隔离 —— 真实用户的会话不会被碰。
- `python -m app.core.test_document_upload`：上传、切块、索引、幂等、删除（17 项）。
- `python -m app.core.test_e2e_http`：**端到端**，打 `127.0.0.1:5173/api`
  （即前端真正请求的地址），覆盖注册→建项目→上传→开始面试→逐题作答→报告→清理（26 项）。
  这是唯一同时验证后端进程、Vite 代理与全部前端端点的脚本。
- `python -m app.core.check_orphan_vectors [--purge]`：Milvus 孤儿向量检查/清理（D52）。
  **清理测试用户或项目之后必须跑一次** —— 数据库的级联删除
  （`projects` → `documents` → `document_chunks`）**不会**碰 Milvus 里的向量。
  实测：清掉一个测试用户就产生 8 个孤儿向量，它们会占用 top-k 名额、
  静默降低召回。**清理顺序：先删用户/项目，再 purge 向量。**
- `python -m app.core.check_embedded_consistency [--repair]`：
  向量索引状态**双向**一致性检查（D54）——
  ① chunk 声称已嵌入但 Milvus 缺失；② 文档状态与 chunk 真实情况不符。
- `python -m app.core.ui_walkthrough`：**界面走查**（§27.8）。
  用 Playwright 加载真实前端、走完五页、截图到 `docs/ui-shots/`、
  收集控制台错误。需要 `playwright`（在 `dependency-groups.dev` 里）与
  本机 ms-playwright 的 chromium 缓存；**不跑 `playwright install`**，
  而是指定已有的可执行文件，避免下载几百 MB。
- `python -m app.core.cleanup_test_users` / `cleanup_test_data` /
  `cleanup_failed_documents`：清理验证脚本产生的测试数据。
  **这些脚本会创建真实数据，跑完必须清理**，否则开发库会被测试数据淹没。
  `cleanup_test_users` 里有 `PROTECTED` 白名单 ——
  真实用户绝不能被按前缀误删（曾把真实用户的级联项目删掉过）。

### 测试数据卫生（反复踩到的坑）

写验证脚本时容易只顾"测出结论"，忘了它们会在真实开发库里留下东西。
以下三条是实际踩出来的，已固化进脚本：

1. **测试用户必须自己删掉。** `test_e2e_http` 每跑一次注册一个用户，
   早期版本不删 —— 跑十几次就有十几个 `e2e_xxx`。
   现在它在结束前删用户。
2. **中途失败也要清理。** 用 `try/finally` 包住主体。
   实测：样本文件缺失导致脚本提前退出，留下两个空项目。
3. **删用户/项目之后必须清向量。** 数据库的级联删除
   （`projects` → `documents` → `document_chunks`）**不碰 Milvus**。
   实测：清一个测试用户留下 8 个孤儿向量，它们占用 top-k 名额、
   静默降低召回。现在 `cleanup_test_users` 会**自动收尾**清向量，
   `test_e2e_http` 也会。

测试数据必须与生产资料隔离；Evaluation Runner 的结果要版本化，且不得悄悄忽略失败 case。
`app/core/` 目录已混放"验证脚本"与"框架代码"（`model_json.py` 是框架代码），
后续应把评估相关的公共模块移出 `app/core/`。

---

## 16. 可观测性

当前以本地脚本输出为主。生产化前需要建立以下可观测性：

| 类别 | 需要记录的内容 | 状态 |
| --- | --- | --- |
| **依赖健康** | 数据库 / 迁移版本 / Milvus 一致性 / 向量一致性 / 模型端点 | ✅ **已实现**（`doctor`，见 §16.1）|
| **回归门禁** | 全部验证套件的单一结论，区分代码失败与环境不可用 | ✅ **已实现**（`test_all`，见 §16.2）|
| **请求链路** | request_id 贯穿日志、`X-Request-ID` 响应头、user / session 上下文 | ✅ **已实现**（见 §16.3）|
| **运行日志** | 模型重试、采样失败、慢请求 | ✅ **已实现**（见 §16.3）|
| 日志 | request_id、user/project/document/session ID、错误码、重试次数 | 🔵 |
| RAG 指标 | Recall@K、MRR、filter accuracy、空结果率、检索延迟 | 🟡 Evaluation 阶段开始建立 |
| 索引指标 | pending 数量、索引成功/失败、耗时、孤儿向量数量 | 🟡 `doctor` 已覆盖孤儿向量数量 |
| 模型指标 | 调用耗时、token / 成本、失败率、限流 | 🟡 `doctor --llm` 覆盖可用性与延迟 |
| 面试指标 | 完成率、追问次数、题目覆盖、评价分布 | 🔵 |
| 告警 | 索引持续失败、检索过滤泄漏、模型异常、依赖不可用 | 🔵 |

观测记录不得包含不必要的简历全文、回答全文或 API Key；需要文本排障时应采用脱敏与最小保留原则。

### 16.1 依赖健康检查（`doctor`）

`uv run python -m app.core.doctor [--llm]`

**为什么需要**：`/api/health` 只返回配置里写的服务名与环境，
**不检查任何依赖** —— 后端返回 200 并不代表它能用。

实测踩过三次，每次都要花时间才分清是哪一类：

| 现象 | 真实原因 |
| --- | --- |
| 套件 exit=1、无摘要 | LLM 端点瞬时超时（不是代码回归）|
| 连接被拒 | Docker 没起（不是代码问题）|
| 检索为空 | Milvus 一致性（D53）|

每项依赖**单独探一遍**，失败时第一眼就能定位。检查项：

- PostgreSQL 连通性 + 表数 + 耗时
- 数据库迁移是否在 head（模型加了字段但没跑迁移，
  报错会是 `column does not exist`，看起来像代码 bug）
- Milvus 连通性 + 集合的**一致性级别**（不是 Strong 就自动收紧）
- 向量一致性（孤儿向量）
- `--llm` 额外检查模型与嵌入端点，**并验证维度是 1024**

**两个设计决定**：

1. **健康检查不重试。** 默认 4 次尝试 + 指数退避在不可达端点上
   要耗 45 秒 —— 那会让"检查环境"变成一件慢事，人就不愿跑它。
   因此 `generate` 加了 `max_attempts` 参数，doctor 传 1。
2. **Redis 只报"已配置但未使用"。** 实测 `redis_url` 只出现在
   配置与测试文本里，业务代码从不使用它。报 OK 会让人以为
   缓存已在工作 —— **这个警告本身有价值**：它让"启动了一堆
   用不上的容器"这件事可见，而不靠人记得。

### 16.2 回归门禁（`test_all`）

`uv run python -m app.core.test_all [--offline] [--filter NAME]`

**为什么需要**：有 14 个验证套件，逐个跑既慢又容易漏；
而"这次改动破坏了什么"必须有**单一的、可信的**答案。

**核心设计：三态而不是两态。**

| 状态 | 含义 |
| --- | --- |
| `PASS` | 套件全部断言通过 |
| `SKIP` | 套件明确报告"依赖不可用，未执行"（退出码 2）|
| `FAIL` | 真正的断言失败 |

简单的"过了/没过"会把**端点抖动**判成代码回归 ——
这类误判在本项目已发生多次。因此：

- 门禁**先跑 `doctor`**：服务不可用时直接停下并说明
  "后续失败不能作为代码问题的证据"
- **数据一致性问题（孤儿向量）不拦停**，只提示。
  它不是"服务连不上"，把它当环境故障会让门禁频繁跑不起来 ——
  而人一旦习惯"门禁跑不起来"，它就失去意义了
- `SKIP` 存在时**不声称全绿**（输出写明"结论不完整"），
  但也不判失败
- 需要第二个后端的套件（`test_llm_error_mapping`、`test_chunk_guard`）
  列在"需手动准备环境"里 —— 让门禁强行依赖两个额外端口
  只会让人不愿跑它

`--offline` 跳过所有会调用模型的套件（约 90 秒），
用于快速确认没破坏逻辑。**`needs_llm` 标记必须与实际相符**：
标错会让"离线"仍要等几十秒且因抖动而失败（实测误标过两个套件）。

### 16.3 请求上下文与运行日志

**背景**：代码里此前**完全没有 logging** —— 生产代码只有 5 处 `print`
（在 LLM 服务与两个评价服务里）。排查时看不到"哪个请求、
哪个用户、哪一步"，只能靠复现。

**两条并行的输出约定**（刻意不统一）：

| 位置 | 输出方式 | 理由 |
| --- | --- | --- |
| `app/core/*.py`（验证脚本）| `print` | 输出是给人看的**结论** |
| `app/services` / `routers` | `logging` | 输出是给**运维排查**用的 |

把验证脚本的 `print` 也改成 logging 是错的：那会让"看结果"
需要额外配置日志级别。

**request_id 的设计**：

- 中间件生成（或沿用上游的 `X-Request-ID`），写进日志上下文，
  并通过响应头返回 —— 用户报错时能直接给出这个 ID
- 用 `ContextVar` 而不是模块级变量：FastAPI 并发处理请求，
  模块级变量会让并发请求互相覆盖 ID，**那比没有 ID 更糟**
  （会给出指错方向的线索）
- **中间件不解析用户身份**。用户 ID 要等认证依赖解完 JWT 才知道；
  在中间件里再解一次会让认证逻辑出现第二种实现。改为让
  `get_current_user` 把 `user_id` 写进上下文 —— 它本来就已经
  解出了用户，顺手绑定最省
- 路径里的 `interview_id` 会被写进 `session` 字段：面试相关报错
  几乎都要问"是哪一场"，从路径取比让每个服务自己写更可靠

**日志格式**（无上下文时省掉整个方括号）：

```
23:20:34 INFO  195fbf8010f2 user=3 session=318 app.request: GET /api/interviews → 200（19ms）
```

**为什么不用结构化日志库**：当前输出到控制台，要的是
"带上下文的可读文本"。接 JSON 日志或采集系统时改
`_ContextFormatter` 一处即可，不必现在付出依赖成本。

#### 顺带修掉的一个真实缺陷：`echo=True` 被写死

`create_async_engine(..., echo=True)` 是**硬编码**的调试开关。
它有两个后果：

1. SQLAlchemy 用**自己的 handler** 再打一遍日志，于是每条 SQL
   出现两次、应用的访问日志被淹没；
2. `.env` 里关不掉 —— 属于"调试开关被写进代码"。

已改为 `settings.sql_echo`（默认 `False`）。
需要看 SQL 时在 `.env` 设 `SQL_ECHO=true`。

**启动命令相应变化**：应用自己记录访问日志后，uvicorn 的访问日志
就是重复的，启动时加 `--no-access-log`：

```powershell
uv run uvicorn app.main:app --host 127.0.0.1 --port 8000 --no-access-log
```

（用启动参数而不是改 logger：那属于 uvicorn 的启动策略，
显式参数比隐式改 logger 更可预测。）

---

## 17. ADR：架构决策记录

| 编号 | 决策 | 原因与后果 | 状态 |
| --- | --- | --- | --- |
| ADR-001 | 产品聚焦 AI 面试助手 | 所有能力服务于“资料 → 面试 → 反馈”，拒绝演变为通用多 Agent 平台 | ✅ |
| ADR-002 | 以项目作为资料隔离边界 | 便于候选人区分不同岗位 / 求职目标，也便于权限校验 | ✅ |
| ADR-003 | PostgreSQL 是事实源，Milvus 是派生索引 | 可重建、可审计，避免仅靠向量库保存业务正文 | ✅ |
| ADR-004 | `DocumentChunk.id` 作为 Milvus 主键 | 让检索结果可直接回填正文，并支持确定性删除 / 重试 | ✅ |
| ADR-005 | Milvus 写入使用 upsert | 化解“向量已写入、DB 状态未提交”时的重试冲突 | ✅ |
| ADR-006 | Document 类型进入向量元数据 | 支持 resume / jd / project / code 等检索范围过滤 | ✅ |
| ADR-007 | 先 Evaluation，后 Interview Engine | 避免在无法量化的检索基础上调试提示词与 Agent 行为 | ✅ |
| ADR-008 | Service 依赖 Embedding / VectorStore 抽象 | 可以替换供应商，不把外部细节硬编码到业务流程 | ✅ |
| ADR-009 | 长文档索引转为异步任务 | 规避上传 API 被 Parsing / Embedding 阻塞，并支持重试 | 🔵 |
| ADR-010 | 面试状态机优先于多 Agent 拆分 | 保证会话可恢复、可审计，控制复杂度 | 🔵 |
| ADR-011 | 面试评估数据集固定输入，不用 LLM 现场生成题目 | 输入固定才能把分数变化归因到 Prompt / 模型 / 逻辑；否则无法做跨版本对比 | ✅ |
| ADR-012 | Interview Evaluation 与 RAG Evaluation 分层隔离 | 两者产出语义不同（检索准确率 vs 面试质量），共用目录但代码与报告前缀分离，避免用例与报告混淆 | ✅ |
| ADR-013 | 判定规则使用 token 序列匹配 + 别名表，禁用技术名词做违规判据 | 实测"细节缺失"类报告会含技术名词，用技术名词判定必然误判；否定模式才表达"整条链路缺失" | ✅ |
| ADR-014 | LLM 调用层统一承担空响应 / 截断重试与 JSON 容错解析 | `deepseek-flash` 实测偶发返回空正文；评测链路若在解析层直接抛错会让单次抖动污染整场评估 | ✅ |
| ADR-015 | 评分波动不可作为质量门禁，只能以结构性规则判定 | 实测同一输入同 temperature 的评分极差达 10–20 分，seed 也无法保证收敛；用评分判定会得到噪声结论 | ✅ |
| ADR-016 | 评分采用锚点制，分数由服务端派生而非模型自由填写 | 实测自由打分时同一输入极差达 20 分；锁定档位后平均标准差减半、完全稳定的维度从 1/18 增至 15/18 | ✅ |
| ADR-017 | 回答分析默认采样多次并按多数档位合并 | 单次采样在档位边界会随机跳档（10 次里 9 次同档、1 次跳相邻档），造成 20–30 分极差 | ✅ |
| ADR-020 | 采样数取 **3**，不取 5 | 推测"5 次更稳"被对照实验推翻：同一组 case、同样 repeats=6 下，samples=3 与 samples=5 的结果完全一致（稳定 2/3、最大极差 20、不稳定的都是同一轮）。原因是该推测假设双峰均匀分布，而实测是稳定的 8:2 偏差，3 次与 5 次都能翻转。因此取 3：同样稳定性，成本减半（每轮 6 次调用 → 4 次；耗时 61s → 27s）。采样数必须为奇数 | ✅ |
| ADR-021 | 线上与离线使用**不同的采样数** | 评测需要可复现（3 次采样合并），线上答题需要低延迟（1 次）。把两者绑在同一个参数上必然牺牲一方。代价是**线上分数比评估分数波动更大，两者不可直接比较**，因此报告必须同时记录两套口径 | ✅ |
| ADR-022 | 状态机只定义状态与合法转移，**不承担业务动作** | 状态机是纯逻辑（无数据库访问），可脱离数据库完整测试。业务动作是否推进状态由服务层显式决定，避免状态机变成"隐式流程引擎"而难以追踪。业务接入见 ADR-025 与 §10.3 | ✅ |
| ADR-023 | `evidence_chunk_ids` 必须在**检索发生的那一刻**捕获，而不是事后补查 | 事后重新检索会得到另一批 chunk，无法证明当初用的是哪些。因此 `generate_question_with_evidence` 把检索结果直接带出并落库。同时 `_format_context` 与 `collect_evidence_chunk_ids` 强制按同一顺序遍历，保证"实际依据"与"记录的依据"一致 | ✅ |
| ADR-024 | 通用能力题用显式 `is_general` 列标记，不靠 `evidence_chunk_ids` 是否为空推断 | §9.3 要求"通用能力题要显式标记为 general，不伪装成资料事实"。用空列表推断会把"资料检索失败"误判为"通用题"，两者后果完全不同 | ✅ |
| ADR-025 | 状态推进**只在外层调用成功之后**发生，且与业务数据同一次提交 | 若"先声明意图再执行"，LLM 调用失败会让会话**卡在 `evaluating`** —— 用户既不能继续答题也无法解释。改为先在内存中标状态、最后一次性提交，中途失败时库里仍是 `asking`，可直接重试。代价：`waiting_for_answer` 不作为独立状态持久化 | ✅ |
| ADR-026 | `completed` 是**真终态**，评完不可重评 | 重跑同一场面试只改变 LLM 采样（测量噪声），不产生新信息；评分本身已有 0–25 分极差（ADR-020/021），允许重评会制造假精确。要重练应开新会话，还能天然获得"两次对比"。代价：用户认为评错时无法重评 | ✅ |
| ADR-027 | 暂停必须记录 `resume_status`，`paused` 的可恢复目标**动态计算**而非写死在转移表 | 写死一组"看起来合理"的恢复目标，会把处于 `waiting_for_answer` 的会话错误地拉回 `asking`。改为暂停时记住来源、恢复时回到来源；缺失 `resume_status` 的会话只允许取消，不猜默认值 | ✅ |
| ADR-028 | 题目预算（`max_questions`）**含追问**，且由服务端在答题流程内强制结束 | 若只统计常规题目，一条长追问链完全不受预算约束 —— 每个追问同样消耗一次 LLM 分析 + 一次生成。实测曾因此让预算判断始终少算一道，预算形同虚设 | ✅ |
| ADR-029 | 达到预算时**在答题流程内**完成评价并结束，不要求前端再调 `finish` | 题量到顶是服务端判断，用户并不知道"这题是最后一题"。若返回"没有下一题"再要求前端调 `finish`，中间任何环节失败都会让会话停在 `evaluating` 成为半成品 | ✅ |
| ADR-030 | 未来拆分 Spring Boot 时，**共享 PostgreSQL + 单一写入者**：状态机留在 FastAPI，只给业务侧一个粗粒度投影 | 见 §23。若业务侧拥有会话状态，FastAPI 每个动作都要回调它读写状态 —— 那是把状态机拆成两半（分布式状态机），比"重复实现能力"更糟 | 🟡 待实施 |
| ADR-031 | `interview_sessions.user_id` 是**冗余字段**，所有权校验同时查 `session.user_id` 与 `project.owner_id` | 只信 `user_id` 会漏掉"项目已易主但仍能访问旧会话"；只信 `project.owner_id` 会漏掉"会话创建者已无权"。两者都过才算有权。冗余的理由是免除每次请求的 join，且将来面试数据归 AI 服务后能独立判权 | ✅ |
| ADR-032 | 配额消费发生在**开始面试**时，不在创建会话时 | 创建会话只产生 draft 草稿、零 LLM 调用。若创建即扣额度，"点了新建又没开始"会让用户白损失一场。这是实测中被测试的累计消耗暴露出来的产品问题，不是实现 bug | ✅ |
| ADR-033 | 配额校验**先自增再判定**，且失败必须回滚 | 先判定后自增会让并发请求同时读到 `used=9/quota=10` 并一起通过。先自增则各请求拿到不同计数，只有真正超出的被拒。代价是被拒的请求也计了数 —— 宁可多计不可漏计，回滚保证用户看不到负数余额 | ✅ |
| ADR-034 | 状态轨迹记录**每一次**转移（含中间态），且**与状态变更同一次事务提交** | 记录中间态才能回答"`asking` 到 `asking` 之间发生了什么"。同一次事务是硬约束：若历史先独立提交而状态随后失败，轨迹里会出现一次"从未发生"的转移 —— 比没有轨迹更糟，因为它会把人引向错误方向 | ✅ |
| ADR-035 | LLM 端点从 **deepseek-flash** 换为 **agnes-3.0-flash**（`https://apihub.agnes-ai.com/v1`） | 见 §24。端点满足三项硬性要求（OpenAI 兼容、锚点 JSON 可解析、seed 被接受），且**换端点后旧的评分不可与新分数比较** —— 报告里记录了 model 与 base_url，因此历史报告仍然自解释 | ✅ |
| ADR-036 | Rubric 判据只用**存在性检查**（有/没有某类元素），禁用计数与程度词；并删除削弱判据的跨字段约束 | 实测 4 个不稳定轮次全部落在档位边界（§24.5）。根因是判据本身不锐利：①"至少两处/三项"依赖计数 ②`depth` 的 C 档与总则对同一回答给出相反指示 ③"大部分/少部分要点"无阈值。**并修掉一个真实算分错误**：原 `completeness` 规则让 quality 判 C 的浅回答拿到 90 分完整度 | ✅ |
| ADR-037 | **不再继续收紧 rubric 措辞**；残余评分方差交给采样数 | 三次全量运行的中位数是 4/6 稳定（旧基线 3–4/6），**同量级**。且 `evasive` t1 在旧模型的 9 次运行里也出现 6 次不稳定 —— 换模型、换 rubric 均未消除，证据指向"锚点边界上的固有不确定性"。继续改措辞的边际收益已接近零（§24.11） | ✅ |
| ADR-038 | 增加**月度题目额度**（跨场次，含追问）；两次验额度：LLM 调用前只读检查、落库前原子扣减 | 单场 `max_questions` 描述"这一场想聊多深"，不是成本额度 —— 用户每场设 30 题就等于把上限变成授权。只读检查放调用前是避免白花 LLM 费用；原子扣减放落库前是保证"扣了额度必有题目、有题目必扣额度"（§25） | ✅ |
| ADR-039 | 用**报告对比工具**代替"重跑稳定性"作为换模型的标准动作；分数方差**不再逐次测量** | 分数方差是模型属性，换模型必然作废；而它又不是产品门禁（ADR-015）。逐次花 30 分钟测一个不用作门禁、下次换模型就作废的数字是不划算的。改为：跑 5 分钟评测 → 纯读的对比工具出结论（§26） | ✅ |
| ADR-040 | 文档解析支持 `.txt` / `.md` / `.pdf` / `.docx`；**界面承诺必须等于后端能力**；解析在落盘与建记录**之前** | ① 入口断了就没有后面一切 —— 界面写着支持 PDF/DOCX 而解析器只有 TXT/MD，真实用户上传简历两次全被拒（D55）。② "图片型"文档（导出成图片的 Word/PDF）没有可提取文字，必须**明确报错**并给出可操作建议，而不是返回空内容让用户以为成功。③ 解析放在建记录之前，失败即无残留 —— 否则用户每失败一次库里就多一条没用的 `failed` 记录 | ✅ |

**ADR-021 的取舍与风险：**

- 收益：线上每轮 2 次调用（约 9.5s），离线每轮 4 次（约 27–38s），各取所需。
- 代价：**存在两套分数口径**。如果将来有人拿线上分数与评估报告对比，
  会得出"线上质量下降"的错误结论。
- 缓解：报告里写入两套采样数（`run_config.sampling` / `config.sampling`），
  并在 `note` 字段显式说明不可比较；线上路径的代码注释也标注了这一点。
- 回滚：提高 `settings.online_analysis_samples` 即可让线上与离线口径一致，
  代价是线上延迟成倍增加。
| ADR-018 | 废弃 R2（`forbidden_missing_points`）规则 | 三次实测误报证明该判据原理上无法成立：任何"未说明 X"模式都能被正当用来批"X 讲得不够深"；且该规则冗余，失败模式已由 R4 覆盖 | ✅ |
| ADR-019 | 多次采样的 `missing_points` 按同义聚类去重并截断，不取并集 | 取并集会把同一缺口的多个同义说法全部保留，实测条数从 6 膨胀到 19，违反 §9.3「追问不重复」并让报告无法阅读 | ✅ |

**ADR-016 的取舍：**

- 收益：分数可复现（档位固定），且 `anchor_reason` 保留了判定依据，便于排查。
- 代价：分辨率从 0–100 降为 4 个离散值（`A=90 / B=70 / C=40 / D=15`），
  **无法再表达"同一档内的细微差别"**。相邻档位差 20–30 分，
  意味着一次跳档会带来 20–30 分的极差。
- 实现位置：`app/schemas/interview/answer_analysis.py`（`ANCHOR_SCORES` 是唯一事实源），
  档位描述在 `app/services/interview/answer_analyzer.py` 的 `RUBRIC`。

**已被否决的方案：把 B 拆成 B+ 与 B（五档）。**

动机：四档时 B(70) 与 C(40) 相差 30 分，处于两者之间的回答会被放大成 30 分极差；
直觉上增加中间档可以降低单次跳档幅度。

实测结果（`--repeats 10`）：

| 配置 | 平均极差 | 平均 σ | 极差=0 的维度数 |
| --- | --- | --- | --- |
| 自由打分 | 10.7 | 3.49 | 1 / 18 |
| **4 档（收紧描述）** | **4.2** | **1.79** | **15 / 18** |
| 5 档 | 5.3 | 1.92 | 13 / 18 |

档位使用分布暴露了失败原因 —— 模型在关键维度上**一次都没有选择 B+**：

```text
quality      A=29  B=20  C=3   D=8     ← B+ 使用 0 次
depth        A=20  B=10  C=20  D=10    ← B+ 使用 0 次
completeness A=17  B=4   B+=9  C=20 D=10
```

模型仍然在 C 与 B 之间二选一（`40x1 70x9`、`15x8 40x2`），
增加档位只是让档位表更复杂，**没有降低跳档幅度**，稳定性反而略有下降。

结论：**已回退为 4 档。** 真正起效的是收紧档位描述，
而不是增加档位数量。若将来确需更细区分，应先验证模型会用新增的档位。

**ADR-017 的效果（多次采样合并，生产路径实测）：**

`--repeats 10`、`samples=3`（分析器默认），6 个轮次 × 3 个维度：

| 轮次 | `answer_quality` | `technical_depth` | `completeness` | 极差 |
| --- | --- | --- | --- | --- |
| `rag_backend_interview` t1 | 90 × 10 | 70 × 10 | 70 × 10 | **0** |
| `rag_backend_interview` t2 | 70 × 10 | 40 × 10 | 40 × 10 | **0** |
| `rag_backend_senior_interview` t1 | 90 × 10 | 90 × 10 | 90 × 10 | **0** |
| `redis_cache_follow_up_interview` t1 | 40 × 8, 15 × 2 | 15 × 10 | 15 × 10 | 25 |
| `redis_cache_follow_up_interview` t2 | 90 × 10 | 90 × 10 | 90 × 10 | **0** |
| `evasive_answer_interview` t1 | 70 × 10 | 40 × 10 | 40 × 10 | **0** |

**5 / 6 轮的三项分数在 10 次重复中完全一致。**

唯一残余不稳定的 `evasive` t1 是**真正的双峰分布**：
单次采样的原始档位约 8:2 分裂（而不是 6:4 的模糊边界），
3 次采样中位数因此会随机落在任一侧。
注意该轮的 `technical_depth` 与 `completeness` **10 次全同**，
说明不稳定只局限在 `answer_quality` 这一个偏主观的维度上，
**不构成聚合结论的不稳定**。

对照总结：

| 阶段 | 平均极差 | 平均 σ | 完全稳定的维度数 |
| --- | --- | --- | --- |
| 自由打分 | 10.7 | 3.49 | 1 / 18 |
| 4 档锚点 | 4.2 | 1.79 | 15 / 18 |
| **4 档锚点 + 3 次采样合并** | **≈1.4** | **≈0.2** | **17 / 18** |

**结论：评分一致性问题已解决，不再是 Phase 6 的阻塞项。**

**ADR-016 的一个防御措施：**

模型若完全不返回锚点，派生分数会静默变成 0，
也就是把"评分失败"伪装成"回答很差"。
因此 `analyze()` 在 `has_anchor` 为假时**显式抛错**，而不是接受 0 分。

新增重大架构决策时，必须补充编号、背景、取舍、影响和状态，而不是只在代码注释中记录。

---

## 18. 路线图与阶段出口

| 阶段 | 目标 | 关键产物 | 状态 |
| --- | --- | --- | --- |
| Phase 0 | 工程与身份基础 | FastAPI、SQLAlchemy、PostgreSQL、Redis、JWT、项目隔离 | ✅ |
| Phase 1 | 文档知识基础 | 上传、TXT/MD 解析、Document、Chunk、metadata | ✅ |
| Phase 2 | 向量索引 | Qwen `text-embedding-v4`、1024 维、Milvus V2、Embedding Pipeline | ✅ |
| Phase 3 | 检索基础 | RetrievalService、项目 / 文档类型过滤、正文回填 | ✅ |
| Phase 4 | RAG Evaluation | Dataset、Runner、Recall@1/3/5、MRR、基线报告 | ✅ 已关闭（2026-09-23） |
| **Phase 5** | **Interview Engine MVP** | 会话模型、提问、回答、追问、整场评价、状态机、证据引用、题目预算、按题计量 | **✅ 后端已完成** |
| **Phase 5.6** | **Interview Evaluation** | 数据集、判定规则、报告、稳定性基线、报告对比工具 | **🟡 框架可用**（见 §8A.9 的真实水平） |
| Phase 6 | 评价与成长反馈 | rubric、总结、能力画像、训练建议 | 🟡 **能力画像已完成**（`GET /api/interviews/profile/ability` + `ProfilePage`）|
| **前端** | 让 MVP 可被真实使用 | 登录、项目、上传、面试全流程、历史、能力画像 UI | ✅ **六个页面已完成** |
| Phase 7 | 生产化与优化 | 异步任务、可观测性、缓存、安全、性能与回归门禁 | 🟡 **异步文档索引、依赖健康、回归门禁、日志与 request_id 已完成**（§28 / §16）|

### 当前的下一个出口

**前端面试全流程已完成，链路可被真实用户走通。**

`start` → 逐题作答（后端自动生成追问）→ `finish` → 报告，
四个页面（登录 / 首页 / 面试 / 报告）覆盖该链路。见 §27。

**仍缺、且会阻挡完整体验的**：

| 项 | 影响 |
| --- | --- |
| **资料上传界面** | 后端 `POST /api/projects/{id}/documents` 已可用，但没有界面 —— 用户无法上传简历，面试只能出通用题，MVP 的"资料依据"就落不了地 |
| **面试历史列表** | 后端**没有**"列出我的面试"端点，用户关掉页面后找不回进行中的面试 |

后端侧未做但不阻塞的项：

| 项 | 说明 |
| --- | --- |
| `Interview Planner` 与 `plan_snapshot` | 列已就绪但恒为空；§9.3 的"主题覆盖"式软终止条件依赖它 |
| `paused` 的恢复上下文快照 | 轨迹能答"何时暂停、从哪来"，"停在哪一步"仍需读会话 |
| 按题计量的成本预估 | 只有额度上限，没有"这场会用掉多少"的提示 |
| 线上延迟 | 单轮 15–20s（§24.3）|

在收到明确确认前，不启动语音面试、通用工作流或团队协作相关代码。
---

## 19. MVP / V1 / V2 范围

### MVP：证据驱动的文字模拟面试

目标：一个用户为一个项目上传资料后，可以完成可信的文字面试并得到有依据的反馈。

- ✅ 认证、项目、资料上传与基础 RAG。
- ✅ 检索评估与质量基线（§8.4）。
- ✅ 面试状态机（§10）、资料驱动题目、回答输入、有限追问、整场评价。
- ✅ 题目和反馈中的证据引用（`evidence_chunk_ids`，ADR-023/024）——
  接口在 `questions` / `detail` / `start` 中暴露，通用题用 `is_general` 显式标记。
- ✅ 服务端强制的题目预算与终止条件（ADR-028/029）。
- ✅ 可恢复：暂停/恢复与状态轨迹（ADR-027/034）。
- ✅ 额度：场次 + 月度题目（ADR-032/033/038）。
- 🟡 **前端：面试全流程已完成**（登录 / 首页 / 面试 / 报告四页）。
      资料上传界面、面试历史列表仍缺。

**MVP 完成定义：** 用户能选择项目与岗位目标，完成一轮可恢复的文字面试；
系统展示问题的资料依据，并输出含优势、风险与建议的总结。

**已完成**：`start` → 逐题作答（含自动追问）→ `finish` → 报告，
四个页面覆盖该链路，报告页展示四项分数、优势/风险/建议与完整问答记录。
**仍缺**：资料上传界面（在后端已可直接上传），
以及面试历史列表（后端暂无"列出我的面试"端点）。

### V1：可衡量的个性化训练

- 🔵 更丰富的题目类型与评分 rubric。
- 🔵 JD—简历—项目的能力映射。
- 🔵 多次面试历史、能力趋势、针对性练习。
- 🔵 混合检索、rerank、上下文压缩及 Evaluation 回归门禁。
- 🔵 文档类型扩展与异步处理。

### V2：深度面试体验与规模化

- 🔵 语音输入输出、实时提示与面试回放。
- 🔵 面向特定岗位族的面试模板和评分基准。
- 🔵 更完善的团队 / 教练协作能力（须先完成隐私与授权设计）。
- 🔵 规模化索引、监控、成本治理与更强的安全合规能力。

---

## 20. 当前进度与待办清单

### 已完成 ✅

- [x] FastAPI / SQLAlchemy / PostgreSQL / Redis 基础工程。
- [x] 用户注册、登录、JWT 与项目归属校验。
- [x] Project、Document、DocumentChunk 基础模型与仓储。
- [x] 文档上传、TXT / MD 解析与字符级 Chunking。
- [x] `document_type` 字段、迁移、上传标注与 Milvus 元数据。
- [x] `embedding_status` 字段、迁移和 pending Chunk 查询。
- [x] Qwen DashScope-compatible Embedding：`text-embedding-v4`，1024 维。
- [x] Milvus V2 collection、Cosine、`document_id` / `project_id` / `document_type` 元数据。
- [x] EmbeddingPipeline：批量 Embedding → Milvus upsert → DB 状态更新。
- [x] RetrievalService：Query Embedding、项目 / 文档类型过滤、Chunk 正文回填。
- [x] Evaluation Dataset 初版。
- [x] Embedding Pipeline 与检索的开发验证脚本；临时 Milvus 向量可由 cleanup 脚本清理。
- [x] RAG Evaluation Runner、指标、报告与 `data_coverage` 缺失说明（5 份基线报告）。
- [x] 面试数据模型与迁移：`InterviewSession` / `InterviewQuestion` / `InterviewAnswer` / `InterviewEvaluation`。
- [x] 面试服务：上下文组装、题目生成、逐轮分析、追问生成、整场评价、整场评价落库。
- [x] 面试 Router：创建会话、查询会话。
- [x] Interview Evaluation 框架：数据集、四条判定规则（R1–R4）、报告、稳定性测量。
- [x] 判定规则自测（16 项）与数据集一致性检查。
- [x] LLM 调用层加固：空响应 / 截断重试，`reasoning_content` 兼容，`seed` 透传。
- [x] 模型 JSON 容错解析：剥代码围栏、提取最外层 JSON、补齐截断（10 项单元验证）。
- [x] 面试引擎端到端验证：干净会话下完整链路（问题 → 回答 → 分析 → 追问 → 整场评价）跑通。

### 当前进行中 🟡

- [ ] 扩充并版本化 Interview Evaluation Dataset（当前 4 case / 6 轮，继续补方向）。
- [x] 建立"完整回答不该被冤枉"的上界对照 case（`rag_backend_senior_interview`）。
- [x] `--repeats 10` 重跑稳定性，得到可信基线（结论见 §8A.5）。
- [x] **提升评分一致性**：评分改为锚点制（ADR-016）+ 默认 3 次采样合并（ADR-017）。
      对照实验效果真实（平均极差 10.7 → 4.4）。
      ⚠️ 但 `--repeats 10` 的实际水平是**典型 6 轮中 3 轮仍超阈值**，
      不是"5/6 完全一致" —— 那是最优单次运行，见 D43 / §8A.9。
- [x] **把整场 `evaluator.py` 也改为锚点制**：已完成（D12），逐轮与整场口径统一。
- [x] **持久化 `strengths` / `weaknesses` / `suggestions`**：已完成（D20，迁移 `b41f7c9a2e38`），
      同时新增 `scoring_details` 记录锚点与采样口径（D21）。
- [x] **处理 `missing_points` 条数膨胀**：词面去重 + `max_items=6`（ADR-019 / D18）。
      注：词面去重有上限，未达到真实主题数（D19）。
- [x] **线上与离线采样数分离**：线上 1 次（约 9.5s/轮）、离线 3 次（约 31s/轮），见 ADR-021。
- [x] **补齐 `evidence_chunk_ids`**：已完成（D22 / D23，迁移 `c7d2e5f8a1b4`）。
      资料依据在检索现场捕获并落库；通用题用 `is_general` 显式标记（ADR-024）。
- [x] **实现面试状态机基础设施**：已完成（D24 / D25，§10）。
      10 个状态 + 白名单转移表 + 强制校验 + 转移端点；
      旧值 `created` 兼容且不回填。
- [x] **把业务动作接入状态机**：已完成（ADR-025）。
      生成问题→`asking`、答题→`evaluating`→`asking`、评价→`completed`；
      规则是"只在外层调用成功后才推进状态"。
- [x] **端到端流程验证**：`app/core/test_interview_flow.py`（20 项断言）。
- [x] **答题 / 暂停 / 复盘的 API 层**：已完成（§11）。
      新增 6 个端点：`detail` / `pause` / `resume` / `answer` /
      `questions` / `finish`，并新增 `InterviewFlowService` 承担流程编排。
- [x] **API 层验证**：`app/core/test_interview_api.py`（49 项断言），
      用真实 JWT 覆盖认证、所有权、校验、暂停恢复、开始与完整链路。
- [x] **开始面试的 API 入口**（`POST /{id}/start`）：已完成。
      MVP 的"完成一轮文字面试"现在**通过 HTTP 全程可走通**：
      `start` → `answer`（含自动追问）→ `finish`。
- [x] **暂停/恢复补齐 `resume_status`**（ADR-027，迁移 `161ade6249ee`）。
- [x] **题目预算与终止条件**（§9.3，ADR-028/029，迁移 `6567047c262f`）。
      服务端现在能强制结束一场面试，不再只能等用户点结束。
- [x] **状态转移历史表**（ADR-034，迁移 `2669a0dbeaa6`）：
      `interview_status_history` + `GET /{id}/history`，
      记录每一次转移含中间态与触发原因。
- [x] **架构演进决策**（§23）：共享库 + 单一写入者、
      状态机留 FastAPI、只给业务侧粗粒度投影。
- [x] **`InterviewSession.user_id`**（ADR-031，迁移 `1943f9f1110a`）：
      所有权校验同时查会话与项目两个来源。
      历史数据从 `projects.owner_id` 回填，**不做近似以外的推断**。
- [x] **用量与配额**（ADR-032/033，§23.4 第 2 步）：
      `users.interview_quota` + `interview_usage` + `GET /api/usage`；
      配额在开始面试时消费，耗尽返回 429。
      这是"制造业务压力、让 Spring Boot 将来有内容可承载"的第一块。
- [x] **按题计量**（ADR-038，§25，迁移 `0d6505d1d05a`）：
      新增 `users.monthly_question_quota`（默认 100）与
      `question_generated` 指标，堵住"把每场设成 30 题"的成本漏洞。
      `/api/usage` 改为成对返回两份额度并指出受限方。
- [ ] **按题计量的成本估算**：当前只有额度上限，
      没有"预计消耗多少"的提示。用户开一场 30 题的面试前，
      应能知道这会用掉月度的 30%。
- [ ] **`paused` 的恢复上下文快照**（§10.6）：轨迹现在能回答
      "什么时候暂停的、从哪暂停"，但"停在哪一步"仍需读会话本身。
- [ ] **软性终止条件**：当前只有"题目数量"这一条硬预算。
      §9.3 还提到"主题覆盖"，需要 Interview Planner 才能判断。
- [ ] **线上延迟**：新端点线上单轮约 15–20s，冷启动更久（§24.3）。
      若要改善，方向是"分析用更小的模型、追问合并进同一次调用"，
      而不是降低采样质量。
- [ ] **额度重置的运维入口**：`UsageService.reset` 已实现但无接口
      （有意不暴露给用户 —— 用户能自己重置就等于没有配额）。
- [ ] 实现 `Interview Planner` 与计划快照填充（`plan_snapshot` 列已就绪但恒为空）。
- [ ] 扩充并版本化 Interview Evaluation Dataset（当前 4 case / 6 轮，继续补 jd / project / code 方向）。
- [ ] 扩 RAG Evaluation Dataset 到 jd / project / code 类型。
- [ ] 可选：引入 embedding 做 `missing_points` 语义去重（D19，需先评估成本与收益）。

> 已完成的项（评分一致性、状态机、证据引用、题目预算、
> 状态轨迹、`user_id`、用量与配额、按题计量、
> 报告对比工具）不再列在这里，见上方"已完成"清单与 §17 ADR 表。

### 后续待办 🔵

- [ ] **前端最小可用版本（MVP 阻塞项）**：登录、项目、资料上传、
      面试全流程（`start` → 逐题作答 → `finish` → 报告）、额度提示。
      后端 12 个面试端点已就绪（§11），当前前端仍是 Vite 脚手架。
      没有它，用户无法走到任何后端能力。
- [ ] 定义并接入异步文档索引任务；上传成功后自动推进 `chunked → embedded`。
- [ ] 处理索引失败、重试、任务状态与一致性 reconciliation。
- [ ] 实现 Interview Planner 与 Summary Generator。
- [ ] 统一面试服务分层：把 `interview_session_service.py` 等移入 `app/services/interview/`。
- [ ] 把评估公共模块（如 `model_json.py`）移出 `app/core/`。
- [ ] 补齐 API、测试、日志、指标、权限与安全治理。
- [ ] Performance Refactor：消除 Chunk 创建后逐条 `refresh()` 的 N+1 查询。
- [ ] `datetime.utcnow` 在 Python 3.12+ 已弃用（9 处，全在模型层），
      需迁移为时区感知时间。注意 DB 列当前是 `TIMESTAMP WITHOUT TIME ZONE`，
      因此**要么保持 naive-UTC、要么连同列类型一起迁移**，不能只改一半。
- [ ] `app/evaluation/runner.py` 中硬编码的 `retrieval_config` / `data_coverage` 改为从数据集派生。

### 明确不做 🔴

- [ ] 不将 MindFlow 扩展为通用多 Agent 平台。
- [ ] 不绕过项目所有权做跨用户知识检索。
- [ ] 不在缺少检索评估基线时提前依赖 Agent 提示词掩盖 RAG 质量问题。
- [ ] 不用 LLM 评分均值衡量模型升级效果（见 ADR-015 与 §8A.5）。

---

## 21. 维护规则

1. 每进入新 Phase 前，先写清目标、输入、输出、验收标准和回滚方式。
2. 每次改动 Chunk、Embedding 模型、Milvus schema、检索过滤或 rerank 后，必须运行并保存 Evaluation 报告。
3. 每次新增用户数据、会话状态或外部依赖时，更新数据模型、权限、安全和一致性章节。
4. 不因未验证的“未来性能问题”提前重构；已知 N+1 已登记，按 Performance Refactor 计划处理。
5. 任何偏离“个人资料驱动的 AI 面试助手”目标的功能，都需要先新增 ADR 并明确产品价值。
6. 每次改动面试 Prompt、模型、`temperature` 或评估判定规则后，必须运行
   `python -m app.evaluation.interview.cli` 并保存报告；
   稳定性基线的变更需另跑 `--stability` 并保存 `interview_stability_*.json`。
7. 编辑 `app/evaluation/interview/datasets.py` 后，必须运行
   `python -m app.evaluation.interview.judges`，确认自测与数据集一致性检查通过。
8. 改动评分档位（`ANCHOR_SCORES`）或档位描述（`RUBRIC`）后，
   必须重跑 `--stability --repeats 10` 并确认稳定性未退化。
   这两处是评分一致性的根基，改动风险最高。
9. `DEFAULT_SAMPLES` 影响评分一致性与调用成本（当前 3 次）。
   调低会重新引入档位跳档，调高会线性增加延迟与费用；
   任何调整都必须附带前后稳定性对照数据。
10. **先辨别问题类型：语义问题不得用词面手段解决。**
    改判定规则、去重、匹配逻辑之前，先回答一句
    "这是不是语义问题"。若是，词面启发式必须**预先声明能力边界**
    （能覆盖什么、不能覆盖什么），不允许靠调参数假装解决。
    依据：本项目已三次踩同一个坑（D4 / D19 / D27）。
11. **只改必须改的。** 发现范围外的问题，
    只报告并单独提议，**不夹带修改**。
    依据：曾在做"证据捕获"时顺手改了 `_format_context` 的遍历顺序 ——
    那次判断正确，但同类操作只要判断错一次，
    就是在无人察觉的地方改行为。
12. **连续两次失败必须停下取证据**，不许继续换方案试。
    依据：空响应问题通过连跑 3 次取真实数据才定位；
    而 `APIConnectionError` 那次直接加重试，没先查是否是环境问题。
13. **不可逆或影响范围大的操作，先声明再执行。**
    删除数据、执行迁移、改动仓库结构、覆盖已有文件均属此类。
    依据：风险应按"可逆性 × 影响范围"分级，
    而不是按"看起来危不危险"分级。
14. **动作之间最多一句话。** 推理写进结论，动作前只留一行意图。
    依据：可审查性 —— 长篇推理会让人看不清"正在做什么"。
15. 本文件与代码不一致时，以代码为事实、以本文件为意图；
   发现不一致应当立即修正本文件，而不是让偏差长期存在。


---

## 22. 验证方法论（AI 辅助开发）

本项目由 AI 大量参与实现，因此"如何验证"本身就是需要固化的规则。
以下每条都来自本项目**实际踩过的坑**，不是通用建议。

### 22.1 改验证标准之前，先问"是在修问题，还是在修指标"

这是本项目最重要的一条判据。

正面例子（刹车装对了）：

- ADR-015：评分波动达 10–20 分时，**明确禁止**用评分均值做质量门禁
- ADR-018：R2 规则三次误报后**废弃规则**，而不是继续收紧措辞
- ADR-020：samples=5 被对照实验否定后**回退**，而不是留着"看起来更强"
- D9：样本被静默丢弃导致"稳定"假象 → 加覆盖率门槛
- ADR-025：不提前提交状态，避免卡在 `evaluating`

反面例子（差点拆刹车）：

- 曾想"把稳定性阈值抬到 25 让它通过"，
  但实测的 10–20 波动**本身就是问题**，抬阈值等于让系统永远通过。
- 曾在 D11 修复前把脏数据当成基线做对比，
  得出"波动与质量成反比"的错误结论并写进文档。

**判据**：想让某个门槛通过时，先自问
"如果我把这个门槛调宽，还有别的机制能发现这个问题吗？"
若没有，那是在拆刹车。

### 22.2 报告必须能自证口径

依据：D16 —— 稳定性报告当时不记录采样次数，
导致无法区分 samples=3 与 samples=5 的报告，只能靠时间戳猜，
并直接引发了一次错误对比。

因此凡是会影响结果解释的配置（采样数、口径、模型、阈值），
**必须写进报告本体**，而不是只写在代码或注释里。

### 22.3 测试失败时，先怀疑测试与环境，再怀疑实现

依据（三例，全部是"错误信息指向了错误的地方"）：

1. 端到端流程测试断言"初始状态为 draft"失败。
   原因是 ORM 对象在状态转移后被**同步更新**，断言读到最终值 ——
   是**测试写错**，不是实现有问题。同类错误连续犯了两次。
2. 预算验证突然报"作答返回 409"，看起来像状态机坏了。
   实际是**配额被前几个脚本耗尽**，`start` 以 429 失败，
   会话停在 draft、不处于 asking，因此作答被拒。
3. 轨迹断言报"未记录 interview_started"。
   实际是**测试自己手工做了两次状态转移**推到 planned，
   `/start` 因此没有中间步要执行 —— 测试过程不自然，不是产品问题。

**规则**：断言失败时先确认三件事，再判断实现是否有 bug ——

1. 我断言的是不是我以为的那个值
2. 环境是否具备前置条件（配额、容器、数据状态）
3. 测试走过的路径是否与真实使用路径一致

### 22.4 状态机 / 校验器抓出实现错误，是它的价值而非障碍

依据：D26 —— 我写的 `asking → evaluating` 被状态机拒绝，
才发现必须经 `waiting_for_answer` 两步走。
若没有强制校验，这个非法转移会静默写进库。

**规则**：被校验器拒绝时，先假设**自己的实现错了**，
而不是先放宽校验。

### 22.5 先取证据，再下结论

依据：多次证明"猜测不如跑一次"：

- 空响应 → 写了诊断脚本连跑 3 次，才确认是间歇性而非稳定 bug
- 稳定性 → 才发现样本被丢弃、阈值不可用
- 去重 → 用真实 18 条数据标定，才发现阈值低到 0.30 也只能降到 8 条
- 别名 → 用真实缺口离线验证，把 0/5 修正到 4/4

**规则**：涉及 LLM 行为、统计口径、匹配逻辑的结论，
必须附可复现的实测数据；**不允许只凭推理下结论**。

### 22.6 记录被推翻的结论

计划书里保留了三处**已被证伪**的推测，连同推翻它的数据：

- "波动与回答质量的极端程度成反比"（基于脏数据）
- "采样数取 5 更稳"（对照实验否定）
- "增加评分档位能降低跳档"（实测反而更差）

**理由**：只留正确结论会让人重复尝试同一条死路。
记录失败路径本身就是防复发的机制。

### 22.7 样本量决定结论强度，定向验证不等于整体改善

依据（D46）：我用 5 个样本的定向探针验证 rubric 改动，
得到"4 个目标轮次 4/4 稳定"，据此写下"0/4 → 4/4"。
但全量 `--repeats 10` 三次运行的中位数只有 **4/6**
（旧基线 3–4/6）—— **同量级，不是决定性改善**。
更直接的打脸：`evasive` 的 `depth` 在探针里 5/5 判 D，
全量实测是 **9/10**，探针恰好没抽到那次误判。

**规则**：

1. 判断"稳定性"必须用 `--repeats 10`，**不要用 5 个样本**。
2. **定向验证只能支持"我针对的那几点改善了"**，
   不能外推成"整体改善了"。两者要分开陈述。
3. 单次运行不足以定论 —— 需要多次运行取中位数
   （本项目的稳定性运行跨运行波动达 2/6 ~ 5/6）。
4. 报告结论时写明样本量与运行次数，否则结论强度无法评估。

这条与 22.5（先取证据再下结论）互补：22.5 说**要有证据**，
本条说**证据的强度取决于样本量**。

### 22.8 文件已损坏时，不要用启发式脚本继续改

依据（D40）：为给测试加 `try/finally` 而做缩进重构时，
我先手工只缩进了部分行（**SyntaxError**），
又写了"缩进小于 12 的行补到 12"的启发式脚本 ——
它破坏了行间的**相对**层级；随后一次整段平移，
导致后续所有代码掉进 `else:` 分支。

三轮脚本化修补后文件比开始时更坏。

**规则**：
- 对**已损坏**的文件，启发式补丁会放大破坏，而不是修复
- 此时应当读全文后**整体重写**，这是确定性的
- 动手改缩进前先做可用的备份（本次的备份是在已损坏状态下做的，
  等于没有备份 —— 而仓库没有任何提交，也没有编辑器本地历史可回退）

**并且**：`finally` 这类"无论如何都要执行"的清理，
应当在**写测试的第一版**就加上。本次的教训不是
"清理写错了"，而是"清理放在断言之后，一失败就跳过" ——
这个设计缺陷才是后续三次数据残留的根因。

### 22.9 本节的适用范围

以上规则针对**本项目当前的工作方式**（AI 参与实现 + 人工决策关键取舍）。
其中 22.1–22.5、22.7 属于可复用的工程判据，
22.6、22.8、22.10 属于文档与流程约定，均不绑定具体技术栈。

### 22.10 长测量开始前必须冻结代码

依据（D44）：跑 `--stability --repeats 10`（约 30 分钟）时，
我中途改了 rubric 源文件。Python 在**进程启动时**加载模块，
因此那次运行的前后两半可能用了不同 rubric ——
结果不可比，只能作废重跑，浪费约 20 分钟。

**规则**：

1. 启动长测量（稳定性、批量评估）**之前**确认代码已冻结。
2. 必须改代码时，**先 kill 掉运行**，改完再重跑；
   不要"让它跑完看看，反正改动只影响后面"。
3. 同理：不要在测量期间重建索引、改数据库、改 `.env`。

这条与 22.2（报告要能自证口径）是同一个关注点 ——
**报告必须能说明自己是在什么条件下产生的**。
运行期间改代码会让报告连"自己用了哪个版本"都说不清。

### 22.11 验证时序类问题时，探针不能比真实路径更宽松

依据（D53）：排查"上传后检索不到"时，我写了 6 个探针，
**全部显示正常**，而真实路径是间歇性失败（2/6 命中）。

原因很隐蔽：那些探针在上传与检索之间**做了额外操作**
（打开新会话、查一次数据库、读 chunk 列表），
无意中给了向量索引可见的时间。而真实路径是
"上传返回后立刻检索" —— 恰好卡在不可见的窗口里。

**规则**：

1. 验证"写完立刻读"这类问题时，探针必须复现**最紧的时序**：
   前一个调用返回后**不做任何其他事**，立刻发起下一个。
2. 探针报告的"全部正常"在时序问题上是**弱证据** ——
   要么复现最紧时序，要么把失败路径**反复跑足够多次**
   （单次成功说明不了任何事；D53 的判据来自 `[F,F,T,F,T,F]` 这样的序列）。
3. 看到"偶发失败"时，**不要先怀疑被测代码**，先检查探针与真实路径
   的差异在哪一步 —— 差异本身就是线索。

这条补充了 22.3（先怀疑测试与环境）：22.3 说的是怀疑方向，
这一条说的是**探针本身会因为变宽松而给出假安全**。

---

## 23. 架构演进：何时引入 Spring Boot

状态：🟡 **已定方向，尚未实施**。

背景：外部有一份《MindFlow 后期技术架构规划》，提出最终采用
Spring Boot（业务）+ FastAPI（AI）双后端。本节记录本项目
**采纳什么、拒绝什么、以及拆分的触发条件** ——
避免将来出现"按哪份文档走"的分歧。

### 23.1 采纳的判断

1. **现在不要把 FastAPI 换成 Spring Boot。**
   当前核心链路的难点在 LLM 行为的可靠性，不在 CRUD。
   用 Spring Boot 重写 Controller / Service / Repository
   会把时间花在最不需要动的地方。
2. **FastAPI 作为 AI Service 长期保留。**
   LLM、Embedding、文档解析、向量检索、Prompt 与 Evaluation
   集中在 Python 侧是合理的。
3. **容器化与 Milvus 的运维不该手搓**
   （minikube / K8s 方向合理，但优先级低）。
4. **不要重复实现相同能力** —— 这条原则比它举的例子更重要，
   它本质上是一条**数据所有权**规则。

### 23.2 拒绝或修正的判断

1. **"业务侧拥有 Interview Session"** —— 见 §23.3。
2. **触发条件不能是"AI Interview Engine 完整稳定"这种形容词。**
   形容词无法判定，必须换成可测量的数字（§23.5）。
3. **不能只在 FastAPI 里做 CRUD 就号称"业务能力"。**
   真正该做的是让业务能力**先产生复杂度**（用量、额度、权限、
   历史），Spring Boot 才有东西可承载（§23.4）。
4. **架构图里 `PostgreSQL → Redis` 的从属连线是错的。**
   Redis 是独立缓存，不是 PG 的下游。

### 23.3 核心决策：会话状态归属（ADR-030）

**方案：共享 PostgreSQL + 单一写入者，状态机留在 FastAPI。**

数据所有权划分：

| 数据 | 唯一写入者 | 读取者 |
| --- | --- | --- |
| `users`、`projects`、订阅/额度/订单 | Spring Boot | FastAPI 只读（需要项目归属与额度上限） |
| `interview_sessions` / `questions` / `answers` / `evaluations` | **FastAPI** | Spring Boot 只读（面试历史、报告、计费） |
| Milvus 向量 | FastAPI | 无 |

**为什么不让业务侧拥有会话状态。**
会话状态机与 AI 引擎深度耦合 —— 每一个转移都对应一次
LLM 调用或检索动作：

| 服务 | 对会话状态的依赖 |
| --- | --- |
| `interview_engine_service` | 读 `current_question_index`、推进到 `asking` |
| `interview_turn_service` | 校验 `asking`、写 `waiting_for_answer`/`evaluating`、读证据 |
| `evaluation_service` | 要求 `evaluating`、写 `summarizing`/`completed` |
| `interview_flow_service` | `draft → preparing_context → planned → asking` 全程编排 |

若状态搬到业务侧，FastAPI 的每个动作都要**回调**它读写状态，
等于把状态机拆成两半 —— 这比"重复实现能力"更糟。

**状态机不镜像，只给投影。** 现有 10 个状态混了两层语义：

- 业务关心的：`draft` / `preparing_context` / `planned` / `asking`
- 纯 AI 执行态：`waiting_for_answer` / `evaluating` / `summarizing`

因此业务侧**不允许**自己实现转移规则，只消费一个粗粒度字段：
`active`（未结束）/ `completed` / `cancelled`。

**取舍必须明确**：共享数据库牺牲了"服务可独立演进 schema"。
对单人项目，它换来的是"不需要任何同步代码"。
若将来团队化，再按表拆库。

### 23.4 执行顺序（拆分是被业务压力逼出来的）

```text
1. 终止条件 + 引擎稳定性判据        ← 定义边界、给出可测量门槛  ✅ 已完成
2. 在 FastAPI 内把业务能力做出来：
   user_id / 权限 / 用量 / 额度 / 历史表    ← 制造"业务压力"
3. 抽出 Spring Boot，承载被压力逼出来的那部分业务
4. Gateway / 可观测 / 异步任务
```

**第 2 步是关键**：应该先在 FastAPI 里把业务能力**真正做出来**，
让它产生真实复杂度，然后 Spring Boot 才有内容可承载。
否则会得到一个只有 User/Project CRUD 的空架子 ——
被问"为什么拆"时只能回答"为了简历"。

**为什么顺序不能反**：终止条件会**定义服务边界**。
面试何时结束？
- 若是"AI 觉得信息收集够了" → AI 能力，归 FastAPI
- 若是"套餐只允许 10 题" → 业务能力，归 Spring Boot

边界没定就先拆分，等于把边界问题推到最贵的时候解决。

### 23.5 拆分的触发条件（可测量）

| 判据 | 目标 |
| --- | --- |
| 同输入 10 次重复的评分极差 | 全部 ≤ 10 分（deepseek 口径实测：典型 6 轮中 3 轮超阈值、平均极差约 13；agnes 口径 2/6、17.5 —— 见 D43 / §24.5） |
| Interview Evaluation 失败率 | < 1%（当前未统计） |
| 题目预算与终止条件 | 已实现且有测试 ✅ |
| 端到端链路可复现 | 连续 10 场无失败 |

在引擎仍在抖的时候拆服务，出问题时无法判断是引擎问题
还是集成问题 —— 这才是"先补引擎"的真正理由，
而不是"引擎还不完整"。

### 23.6 与既有 ADR 的关系

拆分时以下既有决策会被冲击，需要重新确认：

- **ADR-001**（不做通用多 Agent 平台）—— 平台化需先修正该条
- **§4.1 分层原则**（Repository 只管 PostgreSQL）——
  跨服务后"Repository"的边界要重新定义
- **ADR-021 / ADR-027**（采样口径分离、`paused` 动态恢复目标）——
  后者依赖 `resume_status`，若状态迁移到业务侧需一并搬走

---

## 24. LLM 端点（ADR-035）

### 24.1 当前配置

| 项 | 值 |
| --- | --- |
| 厂商 | Agnes AI |
| Base URL | `https://apihub.agnes-ai.com/v1` |
| 模型 | `agnes-3.0-flash` |
| 协议 | OpenAI 兼容 `/chat/completions` |

配置在 `.env`（已 gitignore），模板见 `.env.example`。
代码侧**不含任何厂商专有逻辑** —— `openai_llm_service.py`
只依赖 OpenAI 兼容协议。

### 24.2 切换端点时必须满足的三项硬性要求

换端点前应先用探针验证（本项目就是这么做的）：

1. **能稳定返回非空 `content`。**
   空响应会触发 `LLMEmptyResponseError` 并整轮重试。
   注意有些推理模型把正文放在 `reasoning_content`，
   `_extract_content` 已兼容，但**优先要求 content 有内容**。
2. **能按提示词返回可解析的锚点 JSON。**
   分析器与评价器都依赖结构化输出；
   探针必须用**项目的真实提示词**（2000+ 字符）验证，
   而不是用一句"输出 JSON"糊弄 —— 后者通过不代表长提示词也通过。
3. **`seed` 被接受（或至少被忽略而不报错）。**
   稳定性评估会传 seed。

### 24.3 agnes-3.0-flash 的实测表现

探针结果（2026-10-08）：

| 检查项 | 结果 |
| --- | --- |
| 模型可用 | ✅ 12 个可选模型中包含 `agnes-3.0-flash` |
| `content` 非空 | ✅ 未出现空响应 |
| `reasoning_content` | `None`（非推理模型路径） |
| 锚点 JSON | ✅ 三次采样字段完整，`quality/depth/completeness` 齐备 |
| `seed` | ✅ 接受 |
| 真实提示词（2060 字符） | ✅ 三次采样锚点一致（均 B），延迟 5.4–9.2s |

**延迟画像（重要）**：

| 场景 | 实测 |
| --- | --- |
| 首次调用（冷启动） | 17–38s |
| 短提示词（预热后） | 1.1–7.2s |
| 真实分析提示词（预热后，输出 ~350 tokens） | 5.6–9.9s，平均 7.8s |

因此**线上单轮（分析 + 追问）约 15–20s**，冷启动时可能更久。
这是当前最大的可用性代价。

### 24.4 顺序推理变慢不能归因于模型

同一个输入重复三次给 `45 / 15 / 15`（30 分极差）——
这正是项目采用**锚点 + 多次采样合并**的原因（ADR-016/017），
而不是新模型引入的问题：旧模型在 ADR-015 里记录的极差是 10–20 分。
**结论：换端点不改变"单次采样分数不可信"这个判断。**

⚠️ 同样地，§8A 里所有关于极差、稳定性、去重上限的数字
**都是 deepseek-flash 口径下测得的**。
它们证明的是"单次采样不可信"这类结构性结论（换模型后依然成立），
但具体的数值不适用于 agnes-3.0-flash ——
新口径的稳定性基线需要重跑 `--stability --repeats 10` 才能确定。

### 24.5 agnes-3.0-flash 的稳定性基线（已测）

`--stability --repeats 10` 全量运行结果
（`interview_stability_20261008_204134.json`）：

| 指标 | agnes-3.0-flash | deepseek-flash（8 次 100% 覆盖运行的中位数） |
| --- | --- | --- |
| 稳定轮次 | **2 / 6** | 3 / 6 |
| 最大极差 | 30 | 30 |
| 样本覆盖率 | **100%**（无丢弃） | 100% |
| 每轮平均极差 | 17.5 | 约 13 |

**不稳定的 4 轮，全部落在锚点边界上，且每轮只有一个维度在动：**

| 轮次 | 变动维度 | 10 次取值 | 边界 |
| --- | --- | --- | --- |
| `evasive` t1 | `technical_depth` | `15/40` 交替 | C(40) ↔ D(15) |
| `rag_backend` t2 | `answer_quality` | `40/70`（7:3） | B(70) ↔ C(40) |
| `redis_cache` t1 | `answer_quality` | `40/70`（6:4） | B(70) ↔ C(40) |
| `redis_cache` t2 | `completeness` | `90`×9 + `70`×1 | A(90) ↔ B(70) |

另两轮 **10/10 完全稳定**（`70/70/70` 与 `90/90/90`，极差 0）。

### 24.6 两条结论

1. **换端点没有改变问题的性质。**
   不稳定仍然集中在 `B(70)/C(40)` 与 `C(40)/D(15)` 锚点边界，
   这与 §8A.5C 的原结论一致。**因此对策也不变：收紧锚点描述，
   而不是放松 15 分阈值**（§22.1：那是在修指标不是修问题）。
2. **新模型略差但同量级。** 2/6 对 3/6、平均极差 17.5 对 13。
   考虑到旧基线本身有 4.2~15.8 的巨大跨运行波动
   （见 §8A.9），**这个差距不足以断言新模型更不稳定** ——
   要断言需要多次运行的分布对比，而不是各一次。

⚠️ 单次运行**不足以**比较两个模型。上表并列只是记录事实，
不是"新模型更差"的证据。

### 24.7 换端点后的基线规则

- 历史报告**保留原样**：每份报告都记录了自己的 `llm_model` 与
  `llm_base_url`，因此旧的分数永远能解释自己是在哪个口径下产生的。
- **新旧分数不可直接比较**（与 ADR-021 处理线上/离线口径
  分离是同一条原则）。
- 首次评分评测：`4/4` case、`6/6` 轮，
  R1/R3/R4 通过率均 `1.0000`，报告见
  `evaluation_reports/interview_evaluation_20261008_191123.json`。
- 首次稳定性评测：见 §24.5，
  报告 `interview_stability_20261008_204134.json`。

### 24.8 Rubric 判据重写（ADR-036）

§24.5 的实测显示 4 个不稳定轮次**全部**落在档位边界。逐条诊断后
发现根因不是"模型随机"，而是**判据本身不锐利**：

| 缺陷 | 原措辞 | 问题 |
| --- | --- | --- |
| 依赖计数 | "至少**两处**具体信息""至少**三项**" | 模型对"数到几了"极不稳定 |
| 条款自相矛盾 | `depth` 的 C 档写"只提到技术名词"，总则又写"没有直接回应→最高 C""无可用信息→D" | 对同一类回答给出相反指示，导致 C/D 五五开 |
| 无阈值的程度词 | "大部分要点""少部分要点" | 没有可核对的标准 |
| 跨字段约束不可靠 | "quality 为 C → depth 必须 C" | 实测模型 5 次里只遵守 4 次 |

**改法：全部换成存在性检查（有/没有某类元素）。**

1. `quality` 改为四级顺序问：有无实质内容 → 有无数字 → 有无**具体机制** →
   是否只有态度话。并明确"具体机制"指**怎么做**而不是**用了什么**
   （"我用了 Redis"不算，"用 TTL 让缓存半小时过期"才算）。
   这条修正解决了 `redis_cache` t0 在 B/C 之间摇摆 ——
   原措辞下"缓存"本身算不算机制名是歧义的。
2. `depth` 改为**只看一个事实**：回答里有没有说明某项技术**怎么运作**。
   有→A/B（有取舍/失败处理则 A）；没有→看有无技术名词（有 C / 无 D）。
   删掉与 quality 的跨字段约束（模型执行不可靠）。
3. `completeness` 改为数子问：n = 实质说到的子问数，m = 子问总数。
   **"实质说到" = 既在回应该子问，又给出至少一个数字/机制名/操作步骤。**
   只表明态度不算说到。并特别说明 **m = 1 时不因"提到话题"就判 A** ——
   原规则会让一个 quality 判 C 的浅回答拿到 90 分完整度，
   这是**测量错误**，也是那轮"不稳定"的部分来源。

### 24.9 改动的实测效果

定向验证（4 个已知不稳定轮次 × 5 次）：

| 轮次 | 旧 rubric | 新 rubric |
| --- | --- | --- |
| `rag_backend` t1 `quality` | B/C 摇摆 | **B（5/5）** |
| `evasive` t1 `depth` | C/D 交替 | **D（5/5）** |
| `redis_cache` t1 `quality` | B/C 摇摆 | **C（5/5）** |
| `redis_cache` t1 `completeness` | A/B（4:1，且 A 是错的） | **C（5/5）** |

定向验证由 **0/4 稳定提升到 4/4**。

⚠️ **但 5 个样本不足以判定稳定性** —— 这一点我当场验证了：
定向探针显示 `evasive` 的 `depth` 是 5/5 判 D，
而 `--repeats 10` 的实测是 **9/10**（另有 1 次误判 C）。
**5 个样本恰好没抽到那次。** 判定稳定性必须用 `--repeats 10`。

**定向验证 ≠ 全量基线。** 4/4 这个数字只说明"我针对的 4 个轮次
在窄样本下稳定了"，不代表整体改善。整体结果见 §24.10。

### 24.10 全量基线的真实结果（三次运行）

新 rubric 下 `--repeats 10` 跑了三次（代码已冻结，覆盖率均 100%）：

| 运行 | 稳定轮次 | 最大极差 | 平均极差 | 报告 |
| --- | --- | --- | --- | --- |
| ① | 5 / 6 | 25 | 4.17 | `..._224308` |
| ② | 4 / 6 | 25 | 7.5 | `..._232826` |
| ③ | **3 / 6** | **30** | **17.5** | `..._000726` |
| **中位数** | **4 / 6** | **25** | **7.5** | |

对照（均为 100% 覆盖率的全量运行）：

| 口径 | 稳定轮次中位数 | 平均极差中位数 |
| --- | --- | --- |
| deepseek-flash，旧 rubric（8 次） | 3 / 6 | 约 13 |
| **agnes-3.0-flash，旧 rubric**（1 次） | 2 / 6 | 17.5 |
| **agnes-3.0-flash，新 rubric**（3 次） | **4 / 6** | **7.5** |

### 24.11 结论：改动值得保留，但不是它承诺的那种效果

**必须诚实说明两点：**

1. **三次运行呈 5 → 4 → 3 递减**，说明单次运行可信度很低。
   用中位数看，新 rubric 是 **4/6**，旧模型基线是 **3–4/6** ——
   **同量级，只是偏好的方向**。**这不足以宣称"解决了稳定性问题"。**
2. **方向是对的，但余量比预想小。** 定向验证的 4/4 只覆盖了
   我挑出来的 4 个轮次；整体仍有约 2 轮超阈值，
   且**不稳定的位置在运行之间移动**（① 是 `evasive`，
   ② 是 `rag_backend` t1 + `evasive`，③ 是 `rag_backend` t1/t2 + `evasive`）。

**那为什么仍然保留这次改动？** 因为它修掉了**两个真实缺陷**，
与方差数字无关：

1. **`completeness` 的测量错误**：原规则下"单子问 + 提到话题"即判 A，
   导致一个 quality 判 **C** 的浅回答拿到 **90 分完整度**。
   这是**算错分**，不是噪声。修正后该轮稳定判 C。
2. **`depth` 条款自相矛盾**：C 档与总则对同一回答给出相反指示。
   修正后 `evasive` 的 depth 从 C/D 五五开变为 9/10 判 D。

**因此后续不应继续磨 rubric。** 残余波动的证据指向
"模型在锚点边界上的固有不确定性"：
`evasive` t1 在**旧模型的 9 次运行里也出现了 6 次**，
且换模型、换 rubric 后仍是唯一每次都不稳定的轮次。

**把方差压下去的正解是采样数**（ADR-017 已确立的机制）：
线上/离线都用多次采样 + 多数合并，而不是继续收紧措辞。
若要让评分更稳，应评估把离线采样由 3 提到 5 的收益
（ADR-020 曾在旧模型上验证过"提到 5 无收益"，
但那是在旧 rubric 下测的，结论可能已不适用）。

⚠️ 不要调 `--max-range` 阈值。§22.1：那是在修指标不是修问题。

---

## 25. 按题计量（ADR-038）

### 25.1 要堵的漏洞

原来的额度只有一个单位：**面试场次**（`users.interview_quota`）。
而每场题目数由 `InterviewSession.max_questions` 控制，默认 8。

问题在于 `max_questions` 的语义是"**这一场想聊多深**"，
是产品体验参数，**不是成本额度**。于是：

- 用户把每场都设成 30 题 → 成本约为默认的 4 倍
- 那个上限反而变成了"允许 30 题"的授权

**成本主要发生在逐题的 LLM 调用上**，因此计量单位必须包含"题"。

### 25.2 两份额度的分工

| 额度 | 字段 | 单位 | 管什么 |
| --- | --- | --- | --- |
| 场次 | `users.interview_quota` | 场 / 月 | 能开几场 |
| 题目 | `users.monthly_question_quota` | 题 / 月 | 一共能问几题（**含追问，跨场次**）|

默认值：场次 10、题目 100。取 100 的理由是
默认 10 场 × 默认 8 题 = 80，留约 25% 余量，
让"少开几场但每场深聊"的用法不被误伤。

**追问必须计入**：它同样消耗一次分析 + 一次生成。
只统计常规题目会让追问链完全不受额度约束 ——
单场预算（`questions_asked`）曾因此少算过。

### 25.3 两次验额度，位置不可互换

生成一道题目的顺序是：

```text
1. ensure_available（只读）  ← LLM 调用之前
2. 调 LLM 生成
3. consume（原子自增 + 判定）← 落库之前
4. 保存题目 + 推进状态
```

两次都要，且位置不能换：

- **第 1 次放调用前**：LLM 调用是真花钱的。
  若等生成完再判额度，没额度的用户照样能触发一次调用。
- **第 3 次放落库前**：第 1 次只读、不原子 ——
  若其他请求在两步之间把额度用完，第 3 次必须发现。
  `consume` 是"先自增再判定"，是本项目唯一的额度事实来源。

**为什么扣减在落库之前而不是之后**（D48）：
`create_question` 会自行 commit。若先存题目再扣额度，
扣减失败时那次自增会随回滚消失 ——
**题目已落库但额度没扣**，既少算成本又没给用户提示。
改为先扣后存，两者要么都成功、要么都没发生。

### 25.4 失败时的语义（与 ADR-025 一致）

| 情形 | 行为 |
| --- | --- |
| LLM 生成失败 | 未扣额度（第 1 次检查通过但第 3 次没走到），会话留在 `asking`，可重试 |
| 额度不足（`start`） | 回滚计数并返回 429；会话**仍是 draft**（题目未生成） |
| 额度不足（`answer`） | 返回 429 但**不回滚**：回答与分析已提交，回滚会丢掉用户已产生的数据 |

第三条是有意的取舍：**已经发生的 LLM 调用不能因为回滚而"没花钱"**。

### 25.5 接口

`GET /api/usage` 返回**两份**额度，并指出当前受限方：

```json
{
  "period": "2026-10",
  "interviews": {"metric": "interview_started", "used": 3, "quota": 10, "remaining": 7},
  "questions": {"metric": "question_generated", "used": 100, "quota": 100, "remaining": 0},
  "limited_by": "questions",
  "allowed": false
}
```

**为什么必须成对返回**：只报"剩余 7 场"会让人以为还能用 ——
实际题目额度已见底。`limited_by` 明确受限方，
前端不必自己实现"哪个额度更紧"的比较规则。

### 25.6 验证

`app/core/test_question_quota.py`（26 项断言）：

- 纯逻辑：默认值、两份额度上限不同、零额度语义、受限方判定
- **核心漏洞用例**：单场预算设 30、月度题目额度设 3 →
  第 4 题被 429 挡住，且 `limited_by=questions` 而场次额度仍有剩余
- 追问计入额度（+1）
- 额度用尽后新会话也无法开始
- 被拒后用量不上涨

---

## 26. 更换模型的标准流程（ADR-039）

### 26.1 为什么需要固定流程

项目会**反复更换 LLM 端点**（已换过一次：deepseek → agnes）。
每次都要回答一个问题：**新模型有没有让系统变差？**

没有固定流程时，这件事的成本被严重高估：
跑完评测只能人工翻两份 JSON、凭感觉判断，
于是"重跑"从 5 分钟变成"5 分钟加一堆主观判断"，
结果是没人愿意跑，一路攒着"以后再测"。

同时有一个**不该做的事**：逐次重跑 30 分钟的稳定性。
分数方差是**模型属性**，换模型必然作废；
而它**又不是产品门禁**（ADR-015 禁止用评分判定质量）。
花 30 分钟测一个不用作门禁、下次换模型就作废的数字，不划算。

### 26.2 标准流程

```text
1. 改 .env（LLM_BASE_URL / LLM_MODEL / LLM_API_KEY）
2. 端点探针          几秒    —— 验证要点见 §24.2
3. 跑评测            ~5 分钟  —— app.evaluation.interview.cli
4. 跑对比            几秒    —— app.evaluation.interview.report_compare
5. 结论可接受 → 把新报告设为基线
```

第 4 步纯读 JSON，不调 LLM、不访问数据库。

第 5 步：

```bash
uv run python -m app.evaluation.interview.report_compare \
    --set-baseline evaluation_reports/interview_evaluation_<新报告>.json
```

**基线应设为"已确立的参照"，而不是最新那份** ——
设成最新会让自动检测跳过它、拿旧报告当候选，等于反着比（D50）。

### 26.3 对比工具会报什么

| 输出 | 是否门禁 |
| --- | --- |
| 出错轮次 | ✅ 硬失败（退出码 2）|
| 规则通过率倒退（R1/R3/R4） | ✅ 失败（退出码 1）|
| 口径是否可比（model / base_url / 温度 / 采样数） | ✅ 决定下面的分数能否解读 |
| 逐轮档位与判定变化 | 信息 — **不作门禁**（有采样噪声）|
| R1/R3 命中率变化 | 信息 — 有采样噪声 |
| 延迟变化（含倍数） | 信息 |

**关键设计：口径不同时，档位变化会被明确免责。**
工具会打印"**不能**解读为模型变好或变差 ——
只说明换了模型、分数分布变了，这是预期内的"。
否则每次换模型都会看到一堆档位变化，很容易被误读成回归。

### 26.4 退出码

| 码 | 含义 |
| --- | --- |
| 0 | 无问题 |
| 1 | 规则通过率倒退 |
| 2 | 有出错轮次（优先级高于 1）—— 先修稳定性再比分数 |

### 26.5 什么时候仍然需要跑稳定性

`--stability --repeats 10`（30 分钟）降级为**按需**：

- 怀疑有**系统性问题**（不是单次波动），例如某个维度反复跳档
- 改了 rubric 或锚点描述 —— 那是评分行为变更，需要重新量化方差
- 要调整采样数（`offline_analysis_samples`）

日常换模型**不需要**跑它。

### 26.6 工具自身的可靠性

`--self-test`（13 项，不读真实报告）覆盖：

- 完全相同 → 退出码 0、无档位变化
- 规则倒退 → 退出码 1
- 出错轮次 → 退出码 2
- 口径相同时档位变化**不**被免责
- 口径不同时档位变化**被**免责
- 同一 case 的多轮不会被索引覆盖
- 档位提取走 `anchors.<dimension>.anchor` 嵌套结构
- **自动候选排除基线自己** —— 拿基线和它自己比会输出"无变化"，
  看起来像没问题，实际什么都没比（D50）

这个工具会频繁用于判断"有没有变差"，它自己出错会直接误导基线判断，
因此必须有自测。

---

## 27. 前端（面试全流程）

### 27.1 技术选择

| 项 | 选择 | 理由 |
| --- | --- | --- |
| 框架 | Vue 3 + TypeScript | 脚手架已就位 |
| 路由 | vue-router（`createWebHistory`）| 已装 |
| 状态 | Pinia | 已装，只用于认证态 |
| 请求 | axios | 已装 |
| **UI 库** | **不引入** | 当前只有 4 个页面，组件库会带来一整套主题与覆盖规则，维护成本高于收益。样式写在 `src/styles/app.css`，色值与间距集中在 `:root` 变量里 |
| 样式文件 | 新增 `styles/app.css`，**不删**脚手架的 `style.css` | 后者是 Vite 模板产物，留着可随时对照/回退 |

### 27.2 文件结构

```text
web/src/
  api/
    http.ts      axios 实例：Token 注入、错误归一
    types.ts     与后端契约一一对应的类型
    index.ts     各域接口封装（auth / project / usage / interview）
  stores/auth.ts 认证状态（Token 存取统一走 api/http）
  router.ts      路由与守卫
  pages/
    LoginPage.vue      登录 / 注册
    HomePage.vue       额度提示 + 选项目 + 新建面试
    HistoryPage.vue    面试历史（默认只看未完成）
    ProfilePage.vue    能力画像（中位数 / 区间 / 极差 / 趋势）
    ProjectPage.vue    资料上传 / 索引状态 / 重试与删除
    InterviewPage.vue  逐题作答
    ReportPage.vue     报告
  styles/app.css
  env.d.ts       Vue SFC 与 import.meta.env 的类型声明
```

`env.d.ts` 是**必须补的** —— 脚手架没生成它，
因此 `import X from './X.vue'` 与 `import.meta.env.XXX`
都会报 TS2307 / TS2339。

### 27.3 三条与后端约定直接对应的实现决定

1. **前端不重新实现状态机。**
   "能不能暂停 / 能不能结束"由后端判定，前端只用
   `detail.allowed_transitions` 与会话的 `status` 决定显示什么。
   两套规则并存必然逐渐不一致（§11 设计约定第 3 条）。

2. **额度提示用后端的 `limited_by`，不自己比较两个剩余量。**
   后端成对返回场次与题目额度（§25.5），
   "哪个更紧"的判断只应存在于服务端。

3. **`/answer` 返回 `finished=true` 时直接跳报告页。**
   题目预算耗尽时后端会**自动完成评价**并带回结果（ADR-029），
   前端不该再要求用户点"结束" —— 那会让用户以为还有下一题。

### 27.4 当前问题的判定方式

面试页**不维护本地题号**，而是每次从 `/detail` 取题目列表，
取"最后一道未作答的"作为当前题。

理由：本地索引一旦与服务端不一致（刷新、从报告页返回、
额度不足导致的中断），就会指向错误的题。
以服务端的问答状态为准则永远正确。
代价是每次作答后多一次 `/detail` 请求 —— 相对于 LLM 调用的耗时可以忽略。

### 27.5 验证方式（不需要 Docker）

```bash
cd web
npx vue-tsc -b      # 类型检查
npx vite build      # 生产构建
```

当前两项均通过，产物中四个页面各自分块。

**端到端（需要 Docker + 后端）**：`npx vite --port 5173`
经 Vite 代理访问后端 `/api`。代理目标用
`VITE_BACKEND_ORIGIN` 覆盖，默认 `http://127.0.0.1:8000`。

**尚未做端到端验证** —— 验证时 Docker 未运行。
因此下面的"仍缺"里也包含"这条链路还没有在真实后端上跑过一遍"。

### 27.6 资料管理与向量索引（D51 / D52）

这一轮除了上传界面，还修掉了后端两个**静默断点** —— 它们都属于
"不报错但功能不生效"，比崩溃更难发现。

| 缺陷 | 表现 | 修复 |
| --- | --- | --- |
| **D51** 上传后从不建立向量索引 | 文档停在 `chunked`，检索恒为空 → 面试只出通用题。用户看不出哪里不对 | 上传末尾补嵌入；新增 `POST .../embed` 重试；新增 `GET .../documents` 显示状态 |
| **D52** 删除文档留下孤儿向量 | Milvus 命中但回表取不到 chunk，`retrieve` 只是 `continue` —— 仍占 top-k 名额，召回悄悄变少 | `DELETE .../documents/{id}` 先删向量再删记录；向量删不掉则 502 且不删记录 |
| **D53** Milvus `Bounded` 一致性 | 插入后立刻检索**可能落空** → "上传成功但面试只出通用题"，且**间歇性** | `create_collection` 与 `search` 都显式用 `Strong` |

**嵌入为什么是非致命的**：Milvus 未就绪或嵌入服务抖动时，
让上传整体失败会丢掉用户刚传的文件与已完成的切块。
因此失败时保留 `chunked` 状态并如实告知（HTTP 502），
用户可在资料页点「重试索引」。**但状态必须显示出来** ——
这正是 D51 的教训：功能不生效却看不出来，等于没有。

### 27.7 D53 的排查过程（值得记录）

这个缺陷是**端到端验证才暴露出来的**，而且第一版端到端测试是**间歇性通过**的：

| 轮次 | 结果 |
| --- | --- |
| 第 1 次 | 26/26（`evidence=[11,12]`）|
| 第 2 次 | 24/26（`evidence=[]`）|
| 第 3 次 | 23/26（`evidence=[]`）|

排查时我**逐个排除了所有可疑环节**，每一步都用探针实测而不是猜测：

| 排除的假设 | 实测结果 |
| --- | --- |
| 检索文本不合适 | 5 种不同 query **全部**稳定命中 |
| `project_id` 过滤有问题 | 不过滤 / 过滤都命中 |
| `document_type` 过滤有问题 | `resume` 命中，`jd` 正确为 0 |
| 向量没写进 Milvus | 逐块检查，Milvus 里**都在** |
| `embedding_status` 不对 | 全部 `embedded` |
| `build_context` 逻辑错 | 复现调用 → `evidence=[21]`，正常 |
| 进程内调用有问题 | 进程内 3/3、HTTP 3/3 命中 |

**所有环节单独测都正常**，只有"上传后**立即** start"会失败。

最后写的复现探针（刻意不插入任何等待、连跑 6 次）拿到决定性证据：

```
序列: [False, False, True, False, True, False]   → 2/6 命中
```

接着直接对比两种一致性级别（插入哨兵向量后立刻检索 10 次）：

```
默认(Bounded)   立刻可见 10 次中命中 0 次
Strong          立刻可见 10 次中命中 10 次
```

**教训**（补进 §22）：前面那些探针之所以全部"正常"，是因为它们在上传与检索之间做了额外操作（打开新会话、查数据库），
无意中给了索引可见的时间。**探针引入的额外步骤会掩盖时序缺陷** ——
验证并发/时序类问题时，探针必须复现**最紧的时序**，否则会给出"一切正常"的错误结论。

### 27.8 界面走查（D54）

**接口测试与类型检查看不到"页面长什么样"** —— Vue 挂载失败、布局错位、
文案写错、按钮点不动，只有真的渲染一次才能发现。

为此新增 `app/core/ui_walkthrough.py`：用 Playwright 驱动本机 chromium
真实加载前端，走完 **登录 → 首页 → 资料页 → 面试页 → 报告页**，
截图到 `docs/ui-shots/`，并收集控制台错误与页面异常。

它为了登录会**临时改 `mindflow` 的密码，结束时恢复原哈希**
（已验证临时密码随后失效）。

首次运行结果：

| 页面 | 结果 |
| --- | --- |
| 登录页 | 渲染正常 |
| 首页 | 额度、3 个项目、设置项、「管理资料」链接齐全 |
| 资料页 | 类型下拉 5 项、文件选择框、4 行资料 |
| 面试页 | 问题带「资料依据 2 段」标记 |
| 报告页 | 四项分数 + 5 个小节 + 问答记录，8 条建议 |

**控制台错误 0、页面异常 0。**

**唯一发现的问题就是 D54**：资料页显示"还没有可被检索的资料"，
而向量其实都在。根因是 `documents.status` 未随 chunk 同步
（修复 D53 时我只改了 chunk 级）。

报告页的评价质量值得一提：测试用的是**重复粘贴的同一段回答**，
报告准确指出——

> 面对明确的技术追问时，直接重复第一问答案，完全回避核心问题

**低分是正确判定**（回答确实答非所问），说明评价链路是有效的。

### 27.9 资料依据可点开看片段

§9.3 要求资料型问题**可追溯依据**，但此前接口只暴露
`evidence_chunk_ids`（一串数字）。**数字本身建立不了信任** ——
用户想知道的是"系统是不是真读了我的简历"，而不是"它引用了 4 和 3"。

现在面试页与报告页的"资料依据 N 段"都可点开，展开后显示
每段片段正文 + 来源文件名与类型。

**两个设计决定：**

1. **路径带 `question_id`，而不是直接收一串 chunk id。**
   后者会让任何登录用户都能读**任意项目的**资料片段（IDOR）——
   chunk 自带 `project_id`，但没有任何依据能证明调用方有权看它。
   从问题派生则天然受限：问题属于会话，会话已经过所有权校验。
   测试里对"他人的会话"与"别的会话的 question_id"都断言返回 404。

2. **片段按 300 字截断并如实标注。**
   切块本身是 500 字，这里截断是因为前端只是展示依据；
   截断时必须提示"仅显示前 300 字"，否则用户会以为资料就这么短。

**顺序必须与 `evidence_chunk_ids` 一致**（不是数据库返回顺序）：
那个顺序反映了检索结果喂给模型时的位置，重排会让"展示的依据"
与"实际使用情况"对不上 —— 这正是 `context_builder` 里已经注明的约束。

抽成 `EvidencePanel.vue` 是因为面试页与报告页都要用；
两处各写一遍取数、加载态、错误态与失效提示，很快会漂移成两套行为。

### 27.10 切块守卫与历史数据回填

**先更正一处**：下面 27.11 曾把"无 chunk 的文档点重试索引返回 400"
记为待办，但**复现后发现这个状态在当前代码里已不可达**：

| 输入 | 实测行为 |
| --- | --- |
| 纯空白 / 空文件 | 解析阶段就返回 **400**（"没有可提取的文字"）|
| 极短文本（`你好。`）| 正常切成 1 块、正常 indexed |
| `split_text` 对非空内容 | **至少产出 1 块** |

原因是 D51 修复时把解析前移了：记录只在解析通过后才创建，
因此不会出现"已解析但没块"。我记错了，在此更正。

但库里**确实有 4 份**这样的文档（doc 2–5）：163 字符内容、
0 个 chunk、停在 `parsed`。它们来自 **2026-09-14** ——
那时切块还没接入上传流程。所以这是**历史数据**，不是当前缺陷。

两件事都做了：

1. **加守卫，保证不再产生。** 上传流程补一条判断：解析出内容却
   切不出块时，文档标 `failed` 并返回 400，而不是静默留下一个
   检索不到的记录。已用临时后端（把 `split_text` 打成永远返回空）
   实测：返回 400 + 说明字符数 + 留下**可见的** `failed` 记录。

   为什么允许留下 `failed` 记录：记录与文件都已落盘，标 `failed`
   让用户能在资料页看到并删除 —— 比静默消失好。要防的是
   **静默死档**：状态看起来正常却检索不到。

2. **`repair_missing_chunks` 回填历史数据。** 切块参数与上传流程
   **严格一致**（500 / 100）—— 不一致会让回填的文档与正常上传的
   文档在检索表现上不同，而那种差异极难排查。已回填 4 份：
   各切 1 块、嵌入 1 段、状态到 `embedded`，并验证**检索确实命中**。

**顺带两处测试健壮性**：

- `test_interview_flow` 补上"LLM 端点不可用则明确报未执行"的保护
  （与其它套件一致）。它此前会因端点抖动以 exit=1 崩溃、
  看起来像代码回归。
- 该套件的配额恢复移进 `finally`：此前中途抛异常就不恢复，
  会让后续脚本因额度不足而失败 —— 又是一次误判。

### 27.11 能力画像（Phase 6 首项）

`GET /api/interviews/profile/ability` + `ProfilePage`：
把多场面试的评价放在一起看趋势。

**这一项最重要的约束：不能把评分波动说成能力进步。**

本项目实测同一份回答重复评分的极差有 10–20 分（§8A.9），
ADR-015 也明确"不用 LLM 评分均值衡量模型升级效果"。
因此这一页刻意做了四件事：

1. **给中位数 + 区间 + 极差，不给一个光洁的平均分。**
   中位数比均值稳：实测分数分布偶尔是双峰的，
   均值会被单次极端值拖走。
2. **把采样说明放在最前面。** 用户会先看分数再看结论；
   把"这不是精确测量"放页脚等于让他先形成错误印象。
   极差 ≥10 时额外给出警示。
3. **样本 <3 场时不下结论。** `sufficient_samples` 为假，
   前端不显示"优势 / 短板"类判断 —— 一两场的差异
   完全可能是采样噪声（§22.7）。
4. **弱点只列原文，不做自动归类。** 想按主题聚合就得做
   词表或语义匹配，而两者都会**编造不存在的共性**：
   词表匹配会把"未说明 TTL"和"未说明重试"归成一类。
   因此只统计**去空白后完全一致**的重复原文。

**一个必须过滤的噪音**：`mindflow` 名下有 3 条 2026-09-28 的评价，
`strengths` / `weaknesses` / `suggestions` 三组**全空** ——
它们来自 D20 落库之前，只有四项分数与 `feedback`。
不排除的话画像会显示"共 6 次测量、区间 30–90"，
而其中一半是占位数据，**用户无从分辨**。

选择**过滤而不是删除**：那些评价的 `feedback` 是真实内容、
报告页还在用。清理数据不可逆，过滤视图可逆。
`cleanup_legacy_evaluations` 保留了删除能力，但**默认只检查**。

**逐场明细的时间列刻意用完整日期**（不是"3 天前"）：
趋势判断要按时间对齐，相对时间在这里反而难用。

### 27.12 嵌入分批与大文件上传（本轮发现的两个真实缺陷）

**起因**：测量上传耗时，发现 200KB / 800KB 文件返回 **502**。

#### D59 嵌入批量上限导致大文件必然上传失败

`OpenAIEmbeddingService.embed_texts` 此前把整个文档的 chunk
**一次性**发给端点。而端点有批量上限，于是：

| 文件 | 字符数 | 块数 | 修复前 |
| --- | --- | --- | --- |
| 小 | ~2K | 5 | 201 |
| 中 | ~200K | ~450 | **502** |
| 大 | ~800K | ~1800 | **502** |

**触发阈值约 1 万字符（25 块）** —— 一份详尽的中文简历轻易超过。
而报错是 502「服务不可用」，**用户完全看不出问题出在文件长度上**。

**修复：分批 + 自适应上限。**

实测端点声明的上限**会变**：同一端点在两次测量里分别声称
20 与 10，第三次实测真实值是 10（11 条即被拒）。
因此写死任何常量都是错的 —— 那意味着"换端点时上传又开始失败"。

做法：
1. `settings.embedding_batch_size` 作为**初始猜测**（默认 10）
2. `_create_embeddings` 捕获 `BadRequestError`，
   用 `parse_batch_limit` **从报错里解析真实上限**，
   降到该值后递归重试
3. 学到的上限存在**类属性**上 —— 实例属性等于每次请求
   都要重新学一遍，每个文档白费一次被拒的请求

分批**串行**而非并发：顺序保证"向量 ↔ chunk"的对应关系，
错位不会报错、只会让检索命中错误片段；
而并发会触发端点限流、反而更慢。

修复后：200KB → 201（9.4s），800KB → 201（37.6s）。

> **这也给出了异步索引的真实数据**：800KB 上传全同步阻塞 37.6 秒。
> 但真实简历约 5 块、1 秒内完成 —— 因此异步是"大文件才需要"，
> **不是当前的阻塞性问题**。引入异步前应先定任务执行机制（§6）。

#### D60 孤儿向量检测用相似度搜索"枚举"全库

`check_orphan_vectors` 用一次 top-K **相似度搜索**来获取
"Milvus 里有哪些 id"：

```python
hits = await store.search(vector=vectors[0], limit=SCAN_LIMIT)  # 500
milvus_ids = {int(item["id"]) for item in hits}
```

相似度搜索只返回**离查询向量最近**的 K 条。于是：

- 向量数超过 K 时每次只看到 K 条，而且**每次看到的都不同**
  （实测表现为"每清理一次只删掉 1–2 个"，493 → 492 → 490）
- 更糟：**检测本身不可靠** —— 没落进这 K 条的向量永远查不出来，
  孤儿会被判定为"不存在"

**修复**：新增 `MilvusVectorStore.list_all_ids()`（`query` +
过滤表达式 `id >= 0`），这才是真正的枚举。
修复后一次看清全部：532 个向量 → 519 个孤儿 → **一次清理干净**。

`check_embedded_consistency` 的类型 A 有**同样**的问题，
而且后果更隐蔽：它判"chunk 在 Milvus 里**不存在**"，
用 top-K 会产生**假阳性** —— 把真实存在的 chunk 误报为幻影，
然后脚本会去"修复"一个没坏的东西。已一并改为 `list_all_ids()`。

同时把 `delete` 也改成**分批**（每批 500）：批量接口普遍有上限，
一次传 519 个 id 会被拒绝。

#### 顺带：测试的清理必须被断言

`test_document_upload` 的清理放在 `finally` 里，而 `finally`
在 `async with client` **之外** —— 那时客户端已关闭，
HTTP 删除必然失败，退化成直接删库，于是**向量被留下**
（实测 200KB 文档留下 137 个孤儿向量）。

两处修正：
1. 清理移到客户端仍打开时，并**断言清理结果**
   （"删除后向量不再被检索到"）
2. `cleanup` 的直接删库兜底也**同时删向量** ——
   直接删库不经过删除端点，chunk 与向量都会留下

### 27.13 异步文档索引（§28）

**问题**：上传时同步做"解析 → 切块 → 嵌入"。实测 800KB 文档
阻塞 37.6 秒，用户只能等（还要赌代理与网关的超时）。

**方案**：**数据库作队列，worker 用 `FOR UPDATE SKIP LOCKED` 抢占。**

上传只做"落盘 → 解析 → 切块 → 入队 → 返回 202"，
嵌入交给 worker。

| | 同步（改造前） | 异步（改造后） |
| --- | --- | --- |
| 200KB 上传返回 | 9.4s（201）| **0.17s（202）** |
| 800KB 上传返回 | 37.6s（201）| 约 0.5s（202） |
| 索引完成 | 请求内 | 后台，可离开页面 |

**接口契约的两点选择**：

1. **返回 202，不是 201。** 202 的语义是"已受理、处理中"，
   而 201 会声称"创建完成"—— 此刻文档还不能被检索到，
   那是过度承诺。
2. **解析与切块仍同步。** 它们决定文件是否有效
   （类型不支持、扫描件无文字、切不出块），必须立刻告知用户。
   把整个流程丢进后台意味着用户要过一会儿才发现失败。

#### 为什么用数据库当队列，而不是 Redis

Redis 已经在 Docker 里跑着，看起来是"顺手的选择"。
但它目前**完全没被业务代码使用**，引入它意味着多一个
有状态依赖、多一套故障模式。而队列需要的两样东西 ——
**事务与持久化** —— 数据库已经具备，`FOR UPDATE SKIP LOCKED`
正是为这种场景设计的。

代价是轮询（空队列时每 2 秒一次索引查询）。
那个代价远小于引入新基础设施。**Redis 仍然保持未使用状态**
（`doctor` 会提示这一点），这是一致的。

#### 任务领取必须原子

```sql
UPDATE document_index_jobs SET status='running', ...
WHERE id = (SELECT id FROM document_index_jobs
            WHERE status='pending' ORDER BY id
            FOR UPDATE SKIP LOCKED LIMIT 1)
RETURNING id, document_id
```

先 `SELECT` 再 `UPDATE` 会让两个 worker 抢到同一个任务 ——
同一份文档被嵌入两次。危害不是报错，而是**白花钱且完全看不出来**。
已验证：6 个并发 worker 抢 3 个任务，领到 3 个、零重复。

#### 崩溃恢复靠租约

worker 领走任务后崩溃，任务会永远停在 `running`。
`recover_expired_leases` 把超时未完成的任务放回 `pending`。

没有这条时的故障表现是"界面一直转圈、日志里没有任何报错" ——
最难查的一类问题。**未过期的租约不会被抢走**（已断言），
否则正在跑的任务会被第二个 worker 重复执行。

#### 重试上限

`MAX_ATTEMPTS = 3` 之后标 `failed` 并停止重试。
无限重试没有意义：一个因内容问题的文档每次都会同样失败，
而无限重试只会持续消耗端点额度。

#### 入队幂等

`document_id` 唯一约束 + `ON CONFLICT DO NOTHING`。
重复入队（用户连点"重试索引"）不会产生第二个任务。
已 `done` / `failed` 的任务会被**重置为 pending** ——
那是"重试"的语义，与"重复入队"不同。

#### worker 跑在哪里

`WORKER_ENABLED`（默认 true）在应用进程内跑 ——
`uv run uvicorn` 一条命令就能用。
部署多副本时设为 false 并另起独立 worker。

**即便忘了关也不会出错**：`FOR UPDATE SKIP LOCKED` 保证
同一任务只被领一次，多副本只是浪费连接与轮询。

### 27.14 仍缺

| 项 | 为什么重要 |
| --- | --- |
| `docs/ui-shots/` 的版本管理 | 截图是验证产物，是否入库待定（当前已落在 `docs/` 下）|
| 依据片段的原文定位 | 现在能看到片段，但点不到它在原文档里的位置（需要文档预览能力）|

**已完成**：面试历史列表（`GET /api/interviews` + `HistoryPage`）、
资料依据可点开看**完整**片段（`GET .../questions/{qid}/evidence`
+ `EvidencePanel`；原按 300 字截断，后去除 —— 切块上限本就是
500 字，截断只是无谓地隐藏信息）、切块守卫与历史回填。

---

## 28. 异步文档索引

### 28.1 目标与出口

把"嵌入"从请求路径里移出去。入口是上传与重试索引两个端点，
出口是文档进入 `embedded`（可被检索）或 `failed`（需要人介入）。

### 28.2 任务执行机制（§6 要求先定）

**选择：数据库作队列 + `FOR UPDATE SKIP LOCKED`。**

对比过三个方案：

| 方案 | 否决理由 |
| --- | --- |
| FastAPI `BackgroundTasks` | 进程内、随请求生命周期；进程重启即丢任务，且无法做多副本 |
| Redis 队列（Celery / RQ / 自建）| Redis 目前**完全未被业务代码使用**。引入它意味着多一个有状态依赖与一套新的故障模式，换来的是"持久化 + 事务"——**而数据库已经有这两样** |
| **数据库队列** | ✅ 选中。无新依赖，`FOR UPDATE SKIP LOCKED` 正是为抢占式队列设计的，天然支持多 worker |

代价是轮询（空队列时每 2 秒一次索引查询）。接受这个代价 ——
它远小于引入新基础设施。**Redis 因此保持未使用状态**
（`doctor` 会提示），这是自洽的而不是遗漏。

### 28.3 不变量

这四条是这套机制正确性的全部依据，每条都有对应的断言：

| 不变量 | 破坏后的表现 | 实现 |
| --- | --- | --- |
| **任务领取原子** | 同一文档被嵌入两次 —— 白花钱且**完全看不出来** | `FOR UPDATE SKIP LOCKED` 单语句领取 |
| **崩溃可恢复** | worker 崩溃后任务永远 `running`，界面一直转圈、日志无报错 | `lease_expires_at` + `recover_expired_leases` |
| **重试有上限** | 因内容问题的文档无限重试、持续消耗额度 | `attempts >= MAX_ATTEMPTS` → `failed` |
| **入队幂等** | 连点"重试索引"产生多个任务 | `document_id` 唯一约束 + `ON CONFLICT` |

**未过期的租约不会被抢走**同样是断言项 ——
否则正在跑的任务会被第二个 worker 重复执行，
而那正是第一条不变量要防的事。

### 28.4 接口契约

| 端点 | 状态码 | 语义 |
| --- | --- | --- |
| `POST /projects/{id}/documents` | **202** | 已落盘、解析、切块；索引进行中 |
| `POST /projects/{id}/documents/{did}/embed` | **202** | 已重新入队 |

**为什么是 202 而不是 201**：201 声称"创建完成"，而此刻
文档还不能被检索到 —— 那是过度承诺。202 的语义正是
"已受理、处理中"。

**为什么解析与切块仍同步**：它们决定文件是否有效
（类型不支持、扫描件无文字、切不出块），必须立刻告知用户。
把整个流程丢进后台意味着用户要过一会儿才发现失败 ——
那时他已经离开页面了。

### 28.5 状态语义

**没有新增状态。** 复用已有的 `chunked`（"已切块·未索引"）
表示"排队中/索引中"。

前端的"还有资料未索引"提示与"重试索引"动作原本就是
为这个状态设计的 —— 异步化只是让它**变得更常见**
（以前只有异常时才会停在那里）。

### 28.6 worker 的部署位置

`WORKER_ENABLED`（默认 `true`）在应用进程内跑，
`uv run uvicorn` 一条命令即可用。

部署多副本时设为 `false` 并另起独立 worker。
**即便忘了关也不会出错**：`FOR UPDATE SKIP LOCKED` 保证
同一任务只被领一次，多副本只是浪费连接与轮询。

### 28.7 验证

`uv run python -m app.core.test_async_indexing`（16 项）：
上传返回 202 且 < 5 秒、入队幂等、worker 推进到 `embedded`、
**6 个并发 worker 抢 3 个任务零重复**、过期租约被恢复、
**未过期租约不被抢走**、重试上限后标 `failed`、
无 chunk 的文档不被排队。

另有三处既有套件随之更新（它们断言的是旧的同步契约）：
`test_document_upload`（26 项）、`test_e2e_http`（28 项）、
`test_resume_upload`（14 项）—— 都要在断言前
**等待索引完成**，否则会以"检索命中 0 段"失败，
而失败信息指向的是错误的地方。

共享辅助 `test_support.wait_for_document_indexed` 放在
共享脚手架里：三处复制必然漂移，其中一处写错就会变成一次假失败。
