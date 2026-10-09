"""Interview Engine 评估数据集。

设计原则：

1. 问题与回答全部固定，不使用 LLM 现场生成，
   保证不同 Prompt / 模型 / 逻辑版本之间可以横向比较。
2. 期望缺口（expected_missing_points）描述的是
   "一个合格的技术面试官应该继续追问什么"，
   而不是逐字匹配参考答案，因此判定时使用关键词命中。
3. 只保留**能站得住**的判定规则。
   已废弃的规则不再出现在数据集里，见下方说明。

判定规则（与 judges.py 保持一致）：

- **R1 缺口命中**：expected_missing_points 至少命中 1 个。
  期望列表为空时视为无需命中（完整回答不要求必须报缺口）。
- **R3 追问有针对性**：expected_follow_up_keywords 至少命中 1 个，
  且追问不能是"请详细介绍一下"这类空话。
  关键词应包含语义等价词（如 标识 / 对应 / 映射），
  否则模型换个说法就会被误判为跑偏。
- **R4 评分下限**：min_analyzer_score 校验三项评分
  （answer_quality / technical_depth / completeness）
  是否达到该轮应有的水平。门槛取"该回答合理水平的下限"，
  否则完整回答会被低分冤枉。取值必须是锚点分
  （A=90 / B=70 / C=40 / D=15）之一或 0（表示不校验）。

⚠️ **R2（forbidden_missing_points）已废弃，不要再用。**

原意是防止评估器把"链路完整的回答"误判为"整条链路都没讲"。
但该判据在原理上无法成立——任何"未说明 X"模式都能被正当用来
批"X 讲得不够深"。实测三次误报：

1. 用技术名词（Milvus / Chunk）→ 命中"Embedding 与 Milvus 细节缺失"
   这类**合理**缺口。
2. "未说明分块" → 命中"未说明 500 字符分块的**参数依据**"。
3. "未说明简历解析" → 命中"未说明简历解析的**具体实现**"。

而且该规则是冗余的：它要防的失败模式已由 **R4** 覆盖——
若分析器真的冤枉完整回答，评分会掉到门槛以下。
实测印证：完整回答报出 10 条缺口，但 answer_quality = 90，
说明 **missing_points 的详略与评分高低是解耦的**，不应据此判违规。
"""

INTERVIEW_EVALUATION_CASES = [
    {
        "id": "rag_backend_interview",
        "title": "RAG 后端候选人面试",
        "project_id": 3,
        "direction": "考察候选人的后端开发能力和项目实践经验",
        "turns": [
            {
                # 第 1 轮：信息完整的回答。
                # 目的：验证题面理解质量，并确认评估器不冤枉完整回答。
                "question": (
                    "请结合 MindFlow AI 面试助手项目，"
                    "说明你负责的后端架构设计，"
                    "以及简历解析、向量检索、LLM 调用之间的数据流转过程。"
                ),
                "answer": (
                    "我主要负责 MindFlow AI 面试助手的后端开发。"
                    "用户上传简历后，FastAPI 接收文件并解析，"
                    "文档内容保存到 PostgreSQL，然后进行 Chunk 切分。"
                    "Chunk 通过 Embedding 模型转换成向量写入 Milvus。"
                    "用户进入面试后，系统根据当前问题进行向量检索，"
                    "从 Milvus 取回相关 Chunk，再按 Chunk ID 回 PostgreSQL "
                    "获取原始内容，最后把上下文交给大模型生成个性化面试问题。"
                    "我负责文档上传、Chunk 切分、Embedding、Milvus 检索"
                    "以及面试相关后端接口。"
                ),
                "expected_missing_points": [],
                "expected_follow_up_keywords": [],
                # 完整回答的评分门槛。
                # 实测这段回答在完整走通链路的前提下，
                # technical_depth 约 50-55、completeness 约 55-65
                # （因为架构分层、Chunk 策略、Milvus 索引等细节未展开）。
                # 门槛设为 45：低于它才说明分析器在凭模板扣分。
                "min_analyzer_score": 45,
            },
            {
                # 第 2 轮：本次评估最核心的"暴露真实技术缺口"用例。
                # 目的：验证 Follow-up 是否命中真正的技术缺口，
                #       而不是退化成"请详细介绍一下"。
                "question": "Milvus 和 PostgreSQL 之间是如何关联的？",
                "answer": (
                    "使用 Milvus 存储向量，PostgreSQL 保存文本。"
                ),
                "expected_missing_points": [
                    "chunk_id",
                    "metadata",
                    "向量表关联",
                    "top-k",
                    "排序",
                    "过滤",
                ],
                # 追问只要命中其中任意一个即算"有针对性"。
                # 这里刻意包含语义等价词：
                # 实测模型常用"唯一标识 / 对应 / 映射"来表达
                # chunk_id 这层关联，只列字面词会误判为跑偏。
                "expected_follow_up_keywords": [
                    "chunk",
                    "关联",
                    "对应",
                    "标识",
                    "metadata",
                    "字段",
                    "映射",
                    "top-k",
                    "排序",
                    "过滤",
                ],
                # 短回答的评分门槛低于完整回答。
                # 门槛为 0 表示这一轮不校验评分下限，
                # 只校验缺口命中与追问质量。
                "min_analyzer_score": 0,
            },
        ],
    },
    {
        # ================================================================
        # 上界对照 case
        #
        # 目的：验证"信息量充足的专业回答不会被冤枉"。
        #
        # 与 rag_backend_interview 的 turn 1 不同，这里刻意把
        # 分块参数、向量维度、索引类型、一致性机制、可观测性
        # 全部写进回答，用来观察：
        #   - missing_points 会不会显著变少
        #   - 三项评分会不会明显高于简略回答
        #
        # ⚠️ 阈值标定说明：
        #   expected_missing_points 故意留空，min_analyzer_score 暂设 0。
        #   首次运行后按实测分数回填，避免"先拍一个门槛再看结果"。
        # ================================================================
        "id": "rag_backend_senior_interview",
        "title": "RAG 后端资深候选人面试（上界对照）",
        "project_id": 3,
        "direction": "深入考察后端架构、检索链路与数据一致性设计",
        "turns": [
            {
                "question": (
                    "请详细说明你在 MindFlow 项目中负责的 RAG 检索链路，"
                    "包括分块策略、Embedding 选型、向量索引与检索参数的取舍，"
                    "以及 PostgreSQL 与 Milvus 之间的一致性如何保证。"
                ),
                "answer": (
                    "我负责 MindFlow 的检索链路，分四层说明。\n\n"
                    "第一层分块。文档解析后按 500 字符切块、overlap 100 字符，"
                    "overlap 的目的是避免答案被切断在块边界。"
                    "每个 chunk 写入 PostgreSQL 的 document_chunks 表，"
                    "metadata 里带 document_id、project_id、document_type 和 chunk_index。\n\n"
                    "第二层 Embedding。用 text-embedding-v4，1024 维，"
                    "批量调用，按供应商限制分批并带指数退避。"
                    "向量结果不落在 PostgreSQL，避免大字段拖慢事实源。\n\n"
                    "第三层索引与检索。Milvus collection 是 mindflow_chunks_v2，"
                    "主键直接用 PostgreSQL 的 chunk.id，不自动生成；"
                    "索引 AUTOINDEX，距离度量用 COSINE。"
                    "查询时先对 query 做同样模型的 embedding，"
                    "过滤条件带上 project_id 和 document_type，"
                    "top-k 取 5，按相似度降序返回 chunk_id，"
                    "再按 chunk_id 批量回 PostgreSQL 取正文。"
                    "过滤条件必须带 project_id，否则会跨项目泄露资料。\n\n"
                    "第四层一致性。PostgreSQL 是事实源，Milvus 是可重建的派生索引，"
                    "两边没有分布式事务。写入用 upsert，主键是 chunk.id，"
                    "所以「向量已写入但 DB 状态未提交」时重试可以安全覆盖同一向量。"
                    "流程是先写 Milvus，成功后再把 embedding_status 从 pending 改成 embedded。"
                    "删除文档时先删 Milvus 向量再删 DB 记录，"
                    "如果向量删除失败，由定时对账任务扫描孤儿向量并补偿。\n\n"
                    "可观测性方面，每次检索记录耗时、命中数量和过滤条件，"
                    "空结果率作为单独指标上报，方便区分「资料没上传」"
                    "和「检索质量下降」这两种情况。"
                ).strip(),
                "expected_missing_points": [],
                "expected_follow_up_keywords": [],
                # 按实测标定：完整专业回答的三项分数落在 84-88。
                # 门槛取 70，留出约 15 分余量（相当于 §8A.5 观测到的波动上限）。
                # 低于 70 说明分析器开始凭模板扣分，需要排查。
                "min_analyzer_score": 70,
            },
        ],
    },
    {
        # ================================================================
        # 回避型回答 case
        #
        # 目的：验证分析器对"答非所问"给出低分，并列出应有的内容缺口。
        #
        # 与"短回答"的区别在于回答根本没有回应问题（只讲态度与氛围）。
        #
        # ⚠️ 期望列表**不要写"未回答""回避"这类元评价词**。
        # 实测模型不会输出"候选人回避了问题"，而是直接列出
        # "一个合格回答本该覆盖什么"，例如
        # "未说明 chunk 长度的具体取值以及为什么选择这个长度"。
        # 这同样是正确的缺口识别，只是表达方式不同；
        # 用元评价词做期望会把正确行为判成未命中（已踩过）。
        # ================================================================
        "id": "evasive_answer_interview",
        "title": "候选人回避问题的处理",
        "project_id": 3,
        "direction": "考察候选人对具体技术问题的回答能力",
        "turns": [
            {
                "question": (
                    "你们的 chunk 切分是怎么设计的？"
                    "为什么选择这个长度？overlap 取多少？"
                ),
                "answer": (
                    "这个要看具体情况，不同项目不一样。"
                    "我们团队技术氛围很好，大家都很努力，"
                    "平时也会一起讨论方案。"
                    "我觉得做技术最重要的是持续学习，"
                    "遇到问题就去查资料、问同事。"
                ),
                "expected_missing_points": [
                    "切分策略",
                    "切分长度",
                    "切分依据",
                    "参数",
                ],
                "expected_follow_up_keywords": [
                    "切分",
                    "chunk",
                    "长度",
                    "overlap",
                    "重叠",
                    "分块",
                    "参数",
                ],
                "min_analyzer_score": 0,
            },
        ],
    },
    {
        # ================================================================
        # 多轮追问 case
        #
        # 目的：验证追问链条本身是否成立：
        #   1. 第一轮回答刻意遗漏"缓存一致性"这个点。
        #   2. 需要观察生成的追问是否指向该缺口，而不是重复原问题。
        #   3. 第二轮补上完整回答，观察评分是否随之上升
        #      （若两轮评分差异小于波动幅度，说明评分区分度不足）。
        #
        # 该 case 的 R3 只看第一轮：第二轮是终点，不需要再追问。
        # ================================================================
        "id": "redis_cache_follow_up_interview",
        "title": "Redis 缓存追问链",
        "project_id": 3,
        "direction": "考察候选人对缓存设计的理解深度",
        "turns": [
            {
                "question": "你在项目里是怎么使用 Redis 的？",
                "answer": (
                    "我用了 Redis 缓存高频访问的数据，"
                    "这样接口响应会快很多。"
                ),
                "expected_missing_points": [
                    "缓存对象",
                    "key",
                    "TTL",
                    "过期",
                    "一致性",
                    "失效",
                    "淘汰",
                    "穿透",
                    "雪崩",
                ],
                # 追问应指向"缓存了什么 / key 怎么设计 / 何时失效"
                # 这类具体设计，而不是重复"你为什么要用 Redis"。
                "expected_follow_up_keywords": [
                    "缓存",
                    "key",
                    "键",
                    "过期",
                    "ttl",
                    "失效",
                    "一致性",
                    "更新",
                    "淘汰",
                    "穿透",
                ],
                "min_analyzer_score": 0,
            },
            {
                "question": (
                    "请具体说明你缓存的对象、key 的设计方式，"
                    "以及数据更新时如何保证缓存与数据库一致。"
                ),
                "answer": (
                    "缓存对象是简历的解析结果和面试题列表，"
                    "这两类读多写少。"
                    "key 设计成 resume:parse:{document_id} 和 "
                    "interview:questions:{session_id}，"
                    "用业务 ID 做后缀，方便精确失效。\n\n"
                    "过期策略上，简历解析结果 TTL 设为 1 小时，"
                    "面试题列表 TTL 10 分钟，"
                    "因为面试题会随会话推进变化更快。\n\n"
                    "一致性用 Cache-Aside：读的时候先查缓存，"
                    "未命中回源数据库并回填；"
                    "写的时候先更新数据库，再删除缓存，"
                    "而不是更新缓存，避免并发写导致脏数据。\n\n"
                    "删除失败的处理：把失效操作投递到消息队列重试，"
                    "同时 TTL 作为兜底，保证最终一致。"
                    "另外对空结果也缓存一个短 TTL 占位，防止缓存穿透。"
                ).strip(),
                "expected_missing_points": [],
                "expected_follow_up_keywords": [],
                "min_analyzer_score": 0,
            },
        ],
    },
]
