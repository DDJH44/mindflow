# MindFlow Engineering Issues & Technical Debt

> MindFlow AI 面试助手长期工程问题、技术债、风险与优化事项台账  
> 文件用途：持续记录开发过程中发现的问题，并在专项优化阶段统一处理、验证和关闭。

---

## 1. 文档说明

本文档用于记录 MindFlow 开发过程中发现的：

- 工程正确性问题
- 可靠性问题
- 性能问题
- RAG 检索质量问题
- LLM 调用与成本问题
- 架构风险
- 调试代码
- 后续重构与优化事项

### 核心原则

1. **发现问题立即记录。**
2. **阻塞正确性的问题立即处理。**
3. 不影响当前功能的问题，先记录，不在开发主线上反复打断。
4. 性能、可靠性、RAG/LLM Quality 问题进入对应专项阶段统一处理。
5. 每个问题都必须有明确的关闭条件。
6. 修复后必须重新验证，并将状态更新为 `VERIFIED`。
7. 不因为“当前可以运行”而删除历史问题；历史问题本身也是工程决策和演进记录。

---

# 2. 问题分类

| 分类 | 说明 | 默认处理方式 |
|---|---|---|
| Correctness | 当前功能错误、数据错误、流程无法正常完成 | 立即处理 |
| Reliability | 外部 API、不稳定服务、异常恢复等问题 | 记录，必要时立即处理 |
| Performance | 数据库、Embedding、Milvus、LLM 延迟或资源问题 | 专项优化阶段 |
| RAG / LLM Quality | 检索质量、Prompt、上下文、生成质量问题 | 专项优化阶段 |
| Architecture | 架构扩展性、模块边界、领域模型问题 | 根据影响安排 |
| Security | 权限、输入校验、表达式注入等问题 | 根据风险及时处理 |
| Observability | 日志、指标、追踪、调试信息等问题 | 专项完善阶段 |

---

# 3. 优先级定义

| 优先级 | 含义 |
|---|---|
| CRITICAL | 阻塞核心功能、数据安全或系统正确性 |
| HIGH | 对核心链路有明显影响，应优先处理 |
| MEDIUM | 当前可运行，但规模扩大或场景增加后会产生明显影响 |
| LOW | 工程质量或体验优化，可在后续阶段处理 |

---

# 4. 状态定义

| 状态 | 含义 |
|---|---|
| OPEN | 已发现，尚未处理 |
| DEFERRED | 已确认，但暂缓处理 |
| IN_PROGRESS | 正在处理 |
| VERIFIED | 已修复并完成验证 |
| WONT_FIX | 经过评估后确认无需处理 |

---

# 5. 当前工程问题清单

## ISSUE-001：DocumentChunkRepository 存在 N 次额外数据库查询

**分类：** Performance  
**阶段：** RAG / Document Chunk  
**优先级：** MEDIUM  
**状态：** DEFERRED

### 发现背景

当前 `DocumentChunkRepository.create_chunks()` 使用批量 `add_all()` 创建 Chunk，但提交后又逐个执行 `refresh()`：

```python
self.session.add_all(document_chunks)
await self.session.commit()

for chunk in document_chunks:
    await self.session.refresh(chunk)
```

### 现象

假设一个文档被切分为 N 个 Chunk：

```text
INSERT N chunks
    ↓
COMMIT
    ↓
refresh chunk 1
refresh chunk 2
refresh chunk 3
...
refresh chunk N
```

因此可能产生 N 次额外数据库查询。

### 影响

当文档较大、Chunk 数量增加后，会增加：

- PostgreSQL 查询次数
- 数据库网络往返
- 文档处理时间
- 数据库连接占用

### 当前处理

暂不优化。

当前功能已经验证可正常创建 Chunk，因此先保留现有实现。

### 后续方案

进入 Performance Refactor 后检查：

- SQLAlchemy `AsyncSession` 的 `expire_on_commit`
- 是否可以直接使用 `flush()` 后对象
- 是否需要批量重新查询
- Repository 的返回对象策略
- 大文档场景下的实际性能数据

### 验证标准 / 关闭条件

- Chunk 创建功能保持正确
- 不再产生不必要的逐条 `refresh()` 查询，或有明确理由保留
- 性能测试证明优化有效
- 回归测试通过

---

## ISSUE-002：Embedding API 曾出现间歇性 400 UNKNOWN

**分类：** Reliability  
**阶段：** Embedding Pipeline / RAG  
**优先级：** HIGH  
**状态：** DEFERRED

### 发现背景

Qwen Embedding API 测试过程中曾出现：

```text
400
Unexpected error: UNKNOWN
```

不同请求之间存在间歇性差异。

### 影响

Embedding Pipeline 可能出现：

```text
Document
   ↓
Chunk
   ↓
Embedding API
   ↓
请求失败
   ↓
无法进入 Milvus
```

这会影响文档进入 RAG 知识库。

### 当前处理

当前没有加入：

- Retry
- Exponential Backoff
- Timeout
- Rate Limit
- Error Classification

Evaluation Runner 已经能够捕获单个 Case 的异常，避免一个测试请求失败导致整个评估程序直接退出。

### 后续方案

Reliability Refactor：

```text
Embedding Request
      ↓
Timeout
      ↓
Retry
      ↓
Exponential Backoff
      ↓
Error Classification
      ↓
最终失败 / 记录错误
```

### 验证标准 / 关闭条件

- 模拟短暂 API 失败时能够自动恢复
- 超时不会无限等待
- 最终失败能够被明确分类和记录
- 不产生重复或脏数据
- Evaluation 与正式 Pipeline 均通过回归测试

---

## ISSUE-003：RAG Document Recall 与 Chunk Recall 存在差距

**分类：** RAG / LLM Quality  
**阶段：** RAG Evaluation  
**优先级：** HIGH  
**状态：** DEFERRED

### 当前 Baseline

7 个测试 Case：

```text
Cases:       7
Successful:  7
Failed:      0

Document Recall: 1.0000
Filter Accuracy: 1.0000

Chunk Recall@1: 0.5714
Chunk Recall@3: 0.5714
Chunk Recall@5: 0.5714

MRR: 0.5714

Average Latency: 349.28 ms
Min Latency: 224.83 ms
Max Latency: 914.44 ms
```

### 现象

系统可以找到正确的 Document：

```text
Document Recall = 100%
```

但部分测试 Case 没有找到测试数据定义的 Answer-bearing Chunk。

例如：

```text
正确 Document
      ↓
Document 6
      ↓
返回 Chunk 2
      ↓
测试期望 Chunk 1
```

因此可能出现：

```text
Document Recall = 1
Chunk Recall = 0
```

### 影响

对于 AI 面试助手，仅找到正确文档并不足够。

LLM 最终需要得到与当前问题真正相关的具体上下文，否则可能导致：

```text
正确项目
   ↓
错误 Chunk
   ↓
错误 / 不完整 Context
   ↓
面试问题质量下降
```

### 当前处理

保留当前结果作为 RAG Baseline。

暂不调整 Chunk、Top-K、Reranker 等参数。

### 后续方案

专项评估：

- Chunk Size
- Chunk Overlap
- Top-K
- Metadata Filter
- Reranker
- Query Rewrite
- Hybrid Search
- Parent-Child Retrieval
- Context Compression

### 验证标准 / 关闭条件

重新运行固定 Evaluation Dataset，并比较：

- Chunk Recall@1
- Chunk Recall@3
- Chunk Recall@5
- MRR
- Document Recall
- Filter Accuracy
- Latency

优化不能以降低 Document Recall 或 Filter Accuracy 为代价。

---

## ISSUE-004：当前真实测试数据缺少 JD / Project / Code 类型文档

**分类：** RAG / LLM Quality  
**阶段：** RAG Evaluation / Interview Engine  
**优先级：** HIGH  
**状态：** OPEN

### 当前数据覆盖

```text
resume  ✅
other   ✅

jd      ❌
project ❌
code    ❌
```

### 影响

当前只能验证部分 RAG 能力，还不能完整验证：

```text
Resume
+
JD
+
Project
+
Code
        ↓
Candidate Context
        ↓
Personalized Interview
```

因此当前 Evaluation 尚不能证明完整的个性化面试场景。

### 当前处理

Interview Context Builder 已按以下类型设计：

```text
resume
jd
project
code
```

缺少的数据类型返回空结果，不人为制造测试数据。

### 后续方案

建立完整测试数据集：

```text
candidate_resume.md
job_description.md
project_mindflow.md
code_example.md
```

并建立对应 Evaluation Cases。

### 验证标准 / 关闭条件

四种核心资料均有真实测试文档，并完成：

- 上传
- 解析
- Chunk
- Embedding
- Milvus 写入
- Retrieval Evaluation
- Interview Context Evaluation

---

## ISSUE-005：RetrievalService 的 document_type 缺少严格类型约束

**分类：** Architecture / Correctness  
**阶段：** RAG Retrieval  
**优先级：** LOW  
**状态：** DEFERRED

### 当前实现

```python
document_type: str | None = None
```

### 业务上允许的值

```text
resume
jd
project
code
other
```

### 问题

当前方法理论上可以接收任意字符串，例如：

```text
abc
test
xxx
```

这不符合领域模型约束。

### 后续方案

考虑统一使用：

```python
Literal[
    "resume",
    "jd",
    "project",
    "code",
    "other",
]
```

或者建立领域 Enum。

### 验证标准 / 关闭条件

- 非法 document_type 在进入 Retrieval 前被拒绝
- 所有调用方使用统一类型
- API 与 Service 层行为一致

---

## ISSUE-006：Milvus Filter Expression 直接字符串拼接

**分类：** Security / Reliability  
**阶段：** RAG Retrieval  
**优先级：** MEDIUM  
**状态：** DEFERRED

### 当前实现

```python
filters.append(
    f'document_type == "{document_type}"'
)
```

### 当前风险

目前 `document_type` 主要由业务代码控制，因此实际风险有限。

但如果未来直接来自用户请求：

```text
HTTP Request
    ↓
document_type
    ↓
Milvus filter expression
```

则直接字符串拼接会降低安全性和健壮性。

### 后续方案

建立统一 Filter Builder，并进行：

- Enum 校验
- 参数合法性校验
- 表达式转义
- 用户输入隔离

### 验证标准 / 关闭条件

- 用户输入无法破坏 Milvus expression
- 非法 filter 被拒绝
- 正常 filter 行为保持不变
- 增加对应测试

---

## ISSUE-007：Milvus 旧 Collection 与 V2 Collection 并存

**分类：** Architecture / Maintenance  
**阶段：** Milvus Schema Migration  
**优先级：** MEDIUM  
**状态：** DEFERRED

### 当前状态

旧 Collection：

```text
mindflow_chunks
```

新 Collection：

```text
mindflow_chunks_v2
```

V2 已验证包含：

```text
id
document_id
project_id
document_type
vector
```

并完成数据迁移验证。

### 当前处理

旧 Collection 暂未删除。

### 原因

保留迁移期间的回滚与对照能力。

### 后续方案

确认以下条件后再删除旧 Collection：

1. 所有业务代码均使用 V2
2. V2 数据完整
3. RAG Evaluation 正常
4. 没有业务逻辑继续依赖旧 Collection
5. 已完成备份或确认无需回滚

### 验证标准 / 关闭条件

旧 Collection 安全删除后：

- 所有 Retrieval 正常
- Embedding Pipeline 正常
- Evaluation 正常
- 无旧 Collection 引用

---

## ISSUE-008：LLM max_tokens 过小导致最终答案为空

**分类：** Correctness  
**阶段：** Interview Engine / LLM  
**优先级：** HIGH  
**状态：** VERIFIED

### 发现背景

初始测试使用：

```python
max_tokens=100
```

模型返回：

```text
finish_reason = length
reasoning_tokens = 100
content = ""
```

导致：

```python
message.content == ""
```

最终触发：

```text
RuntimeError: LLM 返回内容为空
```

### 根因

当前模型存在 reasoning 消耗。

100 token 的预算被 reasoning 消耗后，没有剩余预算生成最终文本。

### 当前修复

测试改为：

```python
max_tokens=500
```

随后返回：

```text
finish_reason = stop
content != ""
```

LLM 调用成功。

### 后续方案

正式 Interview Engine 仍需建立：

- Token Budget
- Finish Reason 检查
- 空 Content 处理
- 不同任务的输出长度策略
- Timeout
- Retry

### 验证标准 / 关闭条件

当前问题已满足：

- LLM API 正常调用
- `message.content` 非空
- `finish_reason=stop`
- 测试程序成功输出文本

因此当前 ISSUE 已关闭。

---

## ISSUE-009：LLM reasoning tokens 占用较高

**分类：** Performance / Cost  
**阶段：** LLM  
**优先级：** MEDIUM  
**状态：** DEFERRED

### 发现背景

当前成功测试返回：

```text
completion_tokens = 209
reasoning_tokens = 175
```

说明 completion token 中较大比例被 reasoning 消耗。

### 潜在影响

可能影响：

- API 成本
- 响应速度
- 最终输出长度
- 面试实时交互体验

### 当前处理

暂不优化。

当前优先完成 Interview Engine 主链路。

### 后续方案

LLM Optimization 阶段评估：

- Token Budget
- Reasoning 配置
- Model Selection
- Prompt 长度
- Context 长度
- 不同任务的模型策略
- Question Generation 与 Evaluation 是否需要相同模型

### 验证标准 / 关闭条件

建立 LLM Benchmark，至少记录：

- 平均延迟
- completion tokens
- reasoning tokens
- input tokens
- output tokens
- 单次请求成本
- 生成质量

在保持质量基线的前提下完成优化。

---

## ISSUE-010：LLM RAW RESPONSE 调试输出尚未移除

**分类：** Observability / Maintenance  
**阶段：** LLM Service  
**优先级：** LOW  
**状态：** OPEN

### 当前调试代码

```python
print("========== LLM RAW RESPONSE ==========")
print(response)
print("======================================")
```

### 当前目的

用于确认：

- `finish_reason`
- `content`
- `reasoning_content`
- token usage

并定位 `content=""` 问题。

### 风险

正式环境不应该直接打印完整模型响应。

原因包括：

- 日志噪声
- 可能包含用户上下文
- 增加日志成本
- 不利于正式日志体系管理

### 后续方案

验证 LLM Service 稳定后删除 `print()`。

如果需要保留可观测性，应使用正式 logging：

```text
logger.debug(...)
logger.info(...)
logger.warning(...)
logger.error(...)
```

并控制敏感信息。

### 验证标准 / 关闭条件

- 正式代码不存在裸 `print(response)`
- 调试信息通过 logging 管理
- 不记录敏感 Prompt / 用户数据
- 测试仍然可定位 LLM 错误

---
## ISSUE-011：Interview Question Generator 的 LLM Token Budget 不足
**分类**：Reliability / LLM
**优先级**：HIGH
**状态**：OPEN

### 现象：
Question Generator 使用 max_tokens=500 时，
deepseek-flash 的 reasoning_tokens 达到 500，
finish_reason=length，
最终 content 为空。

### 影响：
无法稳定生成面试问题。

### 初步判断：
固定 max_tokens 无法适应当前模型的 reasoning token 消耗。

### 后续方案：
1. 调查当前模型的 reasoning 配置
2. 研究是否可以控制 reasoning budget
3. 调整 Question Generation 的 token budget
4. 考虑结构化输出
5. 增加 finish_reason=length 的异常处理
6. 建立 LLM Benchmark

### 关闭条件：
Question Generator 在正常测试数据下能够稳定返回非空问题，
并且不会因 reasoning token 消耗导致 content 为空。

---
## ISSUE-012
### 问题：
InterviewAnswer 与 InterviewQuestion 当前是一对多关系，
导致 Session 查询 Questions + Answers 时，
同一个 Question 可能返回多条记录。

### 影响：
1. Evaluation Service 自动组装 Interview Content 时可能重复问题。
2. 统计题目数量可能出现错误。
3. 如果用户重复提交答案，旧答案和新答案都会进入评价上下文。

### 当前状态：
OPEN / DEFERRED

### 后续处理：
明确 InterviewAnswer 的业务语义：
- 一个问题只允许一个最终答案
或
- 一个问题允许多次回答，但 Evaluation 只取最终/最新答案。

暂不在 5.5.3 阶段处理。

---

## SSUE-013：Follow-up 缺乏历史上下文去重
### 现象
同一技术缺口被连续追问，第二个 Follow-up 与前一个 Follow-up 高度相似。
### 原因
当前：
follow_up_generator.generate_follow_up(    question=question.question,    answer=answer,    analysis=analysis,)


它主要看到的是：
当前问题
+
当前回答
+
当前分析

但是没有完整看到：
之前的问题
+
之前的回答
+
之前的分析
+
之前已经追问过的方向

所以它不知道：
Redis 一致性这个点已经问过了。

后续解决方向
建立：
Interview Context

例如：
Session
 ├── Question 1
 │    ├── Answer
 │    ├── Analysis
 │    └── Follow-up
 │
 ├── Question 2
 │    ├── Answer
 │    ├── Analysis
 │    └── Follow-up
 │
 └── Current Interview State
       ├── 已覆盖能力
       ├── 已发现缺口
       ├── 已追问主题
       └── 未验证能力

然后 Follow-up Generator 不再只看当前一轮，而是：
当前问题
+
当前回答
+
当前分析
+
历史面试上下文
+
已追问主题

再决定下一问。

---


# 6. Reliability 优化清单

- [ ] Embedding Retry
- [ ] Embedding Timeout
- [ ] Embedding Exponential Backoff
- [ ] Embedding Error Classification
- [ ] LLM Retry
- [ ] LLM Timeout
- [ ] LLM Error Classification
- [ ] Milvus Connection Error Handling
- [ ] PostgreSQL Transaction Error Handling
- [ ] 外部 API Failure Monitoring
- [ ] 失败任务恢复机制
- [ ] Pipeline Partial Failure Handling

---

# 7. Performance 优化清单

- [ ] DocumentChunkRepository N+1 refresh
- [ ] Embedding Batch Size
- [ ] Embedding API Latency
- [ ] Milvus Search Latency
- [ ] PostgreSQL Query Optimization
- [ ] RAG Context Size
- [ ] LLM Token Usage
- [ ] LLM Reasoning Token Usage
- [ ] Interview Response Latency
- [ ] 大文档处理性能
- [ ] 并发面试场景性能
- [ ] 数据库连接池参数

---

# 8. RAG Quality 优化清单

当前 Baseline：

```text
Document Recall = 1.0000
Filter Accuracy = 1.0000

Chunk Recall@1 = 0.5714
Chunk Recall@3 = 0.5714
Chunk Recall@5 = 0.5714

MRR = 0.5714

Average Latency = 349.28 ms
Min Latency = 224.83 ms
Max Latency = 914.44 ms
```

后续评估：

- [ ] Chunk Strategy
- [ ] Chunk Size
- [ ] Chunk Overlap
- [ ] Top-K
- [ ] Reranker
- [ ] Query Rewrite
- [ ] Hybrid Search
- [ ] Metadata Filtering
- [ ] Parent-Child Retrieval
- [ ] Context Compression
- [ ] JD 数据集
- [ ] Project 数据集
- [ ] Code 数据集
- [ ] 多类型 Context 融合

---

# 9. LLM 优化清单

- [ ] Token Budget
- [ ] Reasoning Token Usage
- [ ] Prompt Optimization
- [ ] Context Compression
- [ ] Model Selection
- [ ] Retry
- [ ] Timeout
- [ ] Structured Output
- [ ] Question Generation Prompt
- [ ] Follow-up Generation Prompt
- [ ] Answer Evaluation Prompt
- [ ] Interview Summary Prompt
- [ ] LLM Benchmark

---

# 10. 当前已验证基线

## 10.1 RAG Baseline

测试集：

```text
7 Cases
Successful: 7
Failed: 0
```

结果：

```text
Document Recall = 1.0000
Filter Accuracy = 1.0000

Chunk Recall@1 = 0.5714
Chunk Recall@3 = 0.5714
Chunk Recall@5 = 0.5714

MRR = 0.5714

Average Latency = 349.28 ms
Min Latency = 224.83 ms
Max Latency = 914.44 ms
```

Evaluation Report：

```text
evaluation_reports/
└── rag_evaluation_20260923_142546.json
```

### 当前结论

当前 RAG 基础链路已经验证：

```text
Query
 ↓
Embedding
 ↓
Milvus Filter
 ↓
Vector Search
 ↓
PostgreSQL Chunk
 ↓
Retrieved Context
```

但 Chunk-level Retrieval Quality 仍需要后续专项优化。

---

## 10.2 LLM Baseline

当前模型：

```text
deepseek-flash
```

调用链：

```text
LLMService
    ↓
OpenAILLMService
    ↓
OpenAI-compatible API
    ↓
deepseek-flash
```

当前成功测试：

```text
finish_reason = stop
content != ""
```

测试输出能够正常返回最终文本。

### 已确认问题

使用过：

```text
max_tokens = 100
```

会出现：

```text
finish_reason = length
reasoning_tokens = 100
content = ""
```

改为：

```text
max_tokens = 500
```

后调用成功。

---

# 11. 当前系统验证原则

MindFlow 当前遵循：

```text
先建立正确系统
        ↓
建立可重复的 Baseline
        ↓
记录问题
        ↓
继续完成核心功能
        ↓
进入专项优化阶段
        ↓
优化
        ↓
重新 Benchmark
        ↓
验证
        ↓
关闭 ISSUE
```

而不是：

```text
发现问题
 ↓
马上重构
 ↓
改变架构
 ↓
引入新问题
 ↓
再次重构
```

### 核心原则

> **先保证正确，再保证稳定，再保证性能，最后持续优化质量。**

---

# 12. 后续问题登记模板

以后发现新问题，复制下面模板：

```md
## ISSUE-XXX：问题标题

**分类：** Correctness / Reliability / Performance / RAG / LLM Quality / Architecture / Security / Observability  
**阶段：**  
**优先级：** CRITICAL / HIGH / MEDIUM / LOW  
**状态：** OPEN / DEFERRED / IN_PROGRESS / VERIFIED / WONT_FIX

### 发现背景

### 现象

### 影响

### 当前处理

### 后续方案

### 验证标准 / 关闭条件

### 相关文件

### 相关测试

### 备注
```

---

# 13. 台账维护规则

每次开发发现值得后续处理的问题时：

```text
1. 分配 ISSUE 编号
2. 判断问题分类
3. 判断优先级
4. 记录现象
5. 记录影响
6. 记录当前处理方式
7. 记录后续方案
8. 记录关闭条件
```

修复后：

```text
OPEN
 ↓
IN_PROGRESS
 ↓
VERIFIED
```

如果暂时不处理：

```text
OPEN
 ↓
DEFERRED
```

禁止出现：

```text
“后面再优化”
```

但没有：

- ISSUE 编号
- 原因
- 优先级
- 后续方案
- 关闭条件

---

# 14. 当前项目阶段

当前 MindFlow 已完成的核心基础：

```text
项目 / 文档管理
        ↓
Document Parser
        ↓
Chunk
        ↓
Embedding
        ↓
Milvus
        ↓
RAG Retrieval
        ↓
RAG Evaluation
        ↓
Interview Session
        ↓
LLM Service
        ↓
Interview Context Builder
```

当前正在进入：

```text
5.3 Interview Question Generation
```

下一阶段重点：

```text
RAG Context
      ↓
Candidate Context
      ↓
Question Generator
      ↓
个性化面试问题
```

之后继续完成：

```text
5.4 Answer → Follow-up
5.5 Interview Evaluation
5.6 Interview Engine Evaluation
```

再进入：

```text
Performance Refactor
Reliability Refactor
RAG Quality Optimization
LLM Optimization
```
