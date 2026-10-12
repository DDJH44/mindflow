"""RAG 检索评估数据集。

## 为什么重写（旧版的三个致命问题）

旧版是 7 个 case，且 `expected_chunk_ids` / `expected_document_ids`
**硬编码了当时库里的 id**：

1. **只有 7 个 case** —— Recall 的分辨率只有 1/7 ≈ 14%，
   任何改动的效果都淹没在噪声里。
2. **硬编码 id 会随库状态失效** —— 实测清理文档后，
   旧版期望的 `chunk 1-4` 分别落到了 doc 6/6/7/8，数据集已与库脱节。
3. **期望的素材是垃圾数据** —— 旧版让
   `test_chunk_metadata.txt`（**112 字符、1 块**）
   充当 4 个不同问题的答案。那不是在测检索，是在测巧合。

## 本版的设计

**数据集自带语料。** 评测时把 `DOCUMENTS` 播种到临时项目、
拿真实 id、再测、最后清理。因此可重复、可扩展、覆盖多类型。

**每份材料都 ≥2000 字符（≥6 块）。** 这是硬约束，理由值得写清楚：

> 切块大小是 500 字符。若一份材料只产出 2 块，
> 而检索取 top-5 —— 那么**必然全部命中**，Recall@5 恒等于 1。
> 于是指标对任何改动都不敏感，基线测了也白测。

实测我第一版就犯了这个错（resume 947 字符 / 3 块、jd 571 字符 / 2 块），
所以 `check_eval_dataset` 把这条写成断言。

**marker 必须落在单个块内。** 每个 case 用一个特征子串定位目标块。
若它跨块，就说不清"命中哪一块算对" —— 那种情况下
chunk 级指标会变成编出来的数字，因此校验脚本会直接判失败。

⚠️ **marker 里不得出现英文引号与反斜杠。** 它们会被写进
Python 字面量，一旦包含这些字符就会破坏语法。
（我实测踩过：自动生成的 marker 带引号，直接把模块写坏，
而 `read_text` 读不出这种问题 —— 必须靠 `py_compile` 才能发现。）
"""

from dataclasses import dataclass

# marker 的识别标记。写在这里以便校验脚本统一引用。
MARKER_MIN_LENGTH = 12


@dataclass(frozen=True)
class EvaluationDocument:
    """数据集里的一份材料。"""

    name: str
    document_type: str
    content: str


# ---------------------------------------------------------------- 语料

RESUME = """# 张明 · 后端开发工程师

## 基本信息
硕士研究生，软件工程专业，2028 年 6 月毕业。
求职方向：后端开发与 AI 应用开发，期望工作城市为上海或杭州。
联系方式与作品集见简历附件，代码托管平台上有开源项目与提交记录。

## 技术栈

### 编程语言
Python 3.13 为主力语言，累计约四年；Go 约一年，主要用于写高并发的采集服务；
TypeScript 约两年，用于配合前端调试与写内部工具。
熟悉 Python 的异步编程模型、类型标注与数据校验。

### Web 框架
FastAPI 为主力框架，也用过 Flask 与 Gin。
熟悉 FastAPI 的依赖注入体系、中间件机制与接口文档自动生成，
也了解它的性能边界在哪里。

### 数据存储
PostgreSQL 为主力关系库，Redis 用于缓存与计数，
Milvus 用于存放向量，MinIO 用于对象存储。
熟悉 PostgreSQL 的索引原理、事务隔离级别与查询计划分析。

### 消息与调度
RabbitMQ 与 Celery。了解消息幂等、死信队列与任务重试的设计取舍。

### 容器与部署
Docker、Docker Compose、Nginx。了解多阶段构建与镜像体积优化。

### 可观测性
OpenTelemetry、Prometheus、Grafana。
实践中用请求标识把散落在多个服务里的日志串成一条链路，
这样排查问题时能回答这个报错属于哪一次请求。

## 项目经历

### MindFlow —— AI 模拟面试助手
时间跨度从 2026 年 3 月至今。角色是后端负责人，
独立完成后端架构设计与实现。

项目目标是让系统根据用户上传的简历与岗位描述，
通过检索增强生成个性化的模拟面试问题，并对回答给出有依据的评价。

技术实现上用 FastAPI 构建服务，用 PostgreSQL 存储结构化数据，
用 Milvus 存储一千零二十四维的文本向量，用 Redis 承担限流与配额计数。

文档处理链路是：上传、解析、切块、向量化、写入向量库。
解析支持四种格式；切块使用五百字符窗口与一百字符重叠；
检索时按项目与资料类型做元数据过滤，再回表取原文，
保证每条提问都能追溯到具体的资料片段。

遇到的第一个难点是上传大文件时向量化会让请求阻塞几十秒。
解法是把向量化抽成异步任务队列，用数据库的跳过已加锁行机制实现任务抢占，
并加了租约超时来做崩溃恢复。

为此写下一条不变量：任务领取必须是单条原子语句。
先查询再更新会让两个工作者抢到同一个任务，
后果是同一份文档被嵌入两次，白花钱而且完全看不出来。

第二个难点是向量库的一致性级别。默认级别不保证插入后立即可检索，
而上传流程在插入后立刻把文档标记为已索引，
调用方紧接着就会检索。实测默认级别下插入后立刻检索十次命中零次。

### 订单中心重构
时间跨度从 2025 年 6 月到 12 月。角色是核心开发，
负责状态机与幂等设计。

背景是单体商城系统的订单模块与库存、优惠券、支付强耦合，
一次改动要回归整个链路，发布风险高。

做法是把订单模块拆为独立服务，引入状态机管理订单流转，
用事件驱动解耦库存扣减与优惠券核销，通过幂等键解决重复支付问题。
重构后订单接口的尾部延迟指标从一千二百毫秒降到一百八十毫秒，
发布回归时间从两天缩短到两小时。

## 技能与自我评价

熟悉检索增强链路的关键取舍：切块粒度、召回数量、
是否需要重排、要不要设相似度阈值。
这些取舍我倾向用量化指标驱动，而不是凭手感调整，
因为感觉变好了经常与指标相反。

也清楚当前实现的局限。检索是纯稠密单路召回，
对精确术语与产品型号的匹配弱于语义匹配。
切块是定长字符切分，会把表格和代码块从中间截断。
没有相似度阈值，低分片段照样进上下文。
这些都是已知的改进方向，而不是没想到。

## 学习与积累

持续在做的几件事。
一是读开源项目的源码而不是只用它的接口，
例如读过 FastAPI 的依赖注入实现，
理解了它为什么能做到同一个依赖在同一次请求内只解析一次。
二是把踩过的坑写成文档而不是修完就算，
上面关于一致性级别与任务抢占的描述就来自这种记录。
三是刻意练习把取舍说清楚：知道一个方案能用不难，
难的是说清为什么不用另一个、以及代价是什么。

我不擅长的是纯前端视觉设计，能实现但要靠设计稿，
独立做不出好看的界面。这一点在协作中需要有人补位。


## 其他补充

本科阶段做过一年半的算法竞赛训练，最好的成绩是省级赛区银奖。
那段经历最大的收获不是算法本身，
而是养成了先确认题目约束再动手的习惯。
后来写业务代码时，这个习惯体现在先确认边界条件与失败路径，
而不是先写主流程。

也做过校内开源社区的代码评审工作。
评审中最常提的意见是这个函数的失败路径没有处理，
而不是命名不够好，因为前者会在生产环境出事，
后者只是不够优雅。这个优先级判断我一直保留到现在。


## 其他补充

本科阶段做过一年半的算法竞赛训练，最好的成绩是省级赛区银奖。
那段经历最大的收获不是算法本身，
而是养成了先确认题目约束再动手的习惯。
后来写业务代码时，这个习惯体现在先确认边界条件与失败路径，
而不是先写主流程。

也做过校内开源社区的代码评审工作。
评审中最常提的意见是这个函数的失败路径没有处理，
而不是命名不够好，因为前者会在生产环境出事，
后者只是不够优雅。这个优先级判断我一直保留到现在。

## 对工具与流程的看法

我不太信任靠人记住的规范，更倾向把它变成可执行的检查。
例如本项目的回归门禁与依赖健康检查脚本，
就是把"上线前该确认什么"从口头约定变成了命令。
这样新人接手时不需要先读一遍文档才知道该做什么。

同样地，遇到反复出现的同类问题时，
我的第一反应是写一个能自动发现的检查，
而不是每次都靠人去注意。这一点在下面项目经历里有具体例子。


## 关于协作方式

我习惯在动手前先把方案写成一页纸，包含目标、方案、代价与验收方式。
这一页纸的价值不在于"汇报"，而在于**逼自己把没想清的地方暴露出来** ——
写不出来的部分通常就是还没想明白的部分。

协作中我更倾向把问题在群里说清楚而不是私下解决，
因为同类问题往往不止一个人会遇到。
本项目里关于一致性级别与任务抢占的记录就是这么来的：
先有人问，然后发现值得写下来。
"""


JOB_DESCRIPTION = """# 高级后端开发工程师（AI 应用方向）

## 岗位职责

一、负责 AI 应用的后端架构设计与核心模块开发，
参与技术方案评审并推动落地。

二、设计并实现基于大模型的应用链路，包括但不限于
检索增强、工具调用与多轮对话管理。

三、负责向量检索系统的建设与调优，包括切块策略、
索引选型、召回数量与召回质量评估。

四、参与系统的性能优化与稳定性建设，保障线上服务的高可用，
参与容量评估与故障复盘。

五、参与团队技术方案评审，沉淀可复用的工程规范与文档。

## 任职要求

一、本科及以上学历，计算机相关专业，三年以上后端开发经验。

二、精通 Python，熟悉 FastAPI 或同类异步 Web 框架，
理解异步编程的常见陷阱，例如阻塞调用与连接池耗尽。

三、熟练掌握 PostgreSQL，理解索引原理与查询优化，
能读懂并分析慢查询的执行计划。

四、有向量数据库的实际使用经验，理解向量索引的原理
与近似检索带来的召回损失。

五、熟悉容器化部署，了解持续集成流程与灰度发布。

六、具备良好的沟通能力，能向非技术角色解释技术取舍。

## 加分项

有检索增强系统的检索质量评估经验，能用量化指标驱动优化，
而不是凭主观判断感觉变好了。

理解嵌入模型的选型权衡，包括维度、调用成本与多语言表现。

有异步任务队列的实践经验，理解任务幂等与失败重试的设计，
并且知道任务卡住这类故障为什么最难排查：
它通常既不报错也不超时，只是永远不完成。

熟悉分布式追踪等可观测性体系，有排查线上问题的实战经验。

有技术文档写作习惯，能把决策的取舍写清楚而不只是记结论。

## 团队情况

团队规模为八人，负责公司智能中台的能力建设，
包含检索、推理服务与数据管道三个方向。

技术栈以 Python 为主，正在从单体架构向领域服务拆分演进。
团队重视工程质量：有代码评审、有自动化测试，
关键链路上有回归门禁，改动必须通过评估集回归才能合并。

日常沟通扁平，技术方案由写代码的人主导，不设专门的评审委员会。

## 面试流程

技术面试共三轮：初面一小时含编码环节，
复面一小时以系统设计为主，最后一轮是三十分钟的沟通面。

技术面会深入追问简历上的项目细节，
因此对自己写过的项目要能说清取舍与踩坑，
而不是只描述用了什么技术。

建议准备两个方向：一个是最熟悉的项目的完整链路，
另一个是踩过的最深的坑以及最后怎么定位的。
后者往往更受关注，因为它体现的是排查能力而不只是实现能力。

## 工作方式

团队用两周一个迭代，需求评审与技术方案评审分开。
技术方案评审不是走过场：方案里必须写清
为什么选这个方案、代价是什么、失败了怎么退，
没写清会被打回重做。

线上问题有明确的复盘机制：不追责个人，
但要求把根因和下次怎么提前发现写进文档。
我们更看重后者，因为同一个问题重复出现才算事故。


## 招聘说明

这个岗位不是从零搭建新系统，而是在已有链路上做优化与扩展。
因此我们更看重候选人**判断改动好坏的能力**，
而不只是实现能力：能说清为什么这么改、
怎么验证改对了、以及代价是什么。

面试中我们会请你讲一个具体的排查过程，
重点不是结果，而是你如何缩小范围、
如何区分"环境问题"与"代码问题"、
以及在信息不足时怎么决定下一步查什么。

## 关于团队氛围

我们不追求技术栈新潮，更在意把已有的东西用对。
评审时欢迎反对意见，但要求给出替代方案与代价，
而不是只说"我觉得这样不好"。


## 招聘说明

这个岗位不是从零搭建新系统，而是在已有链路上做优化与扩展。
因此我们更看重候选人判断改动好坏的能力，
而不只是实现能力：能说清为什么这么改、
怎么验证改对了、以及代价是什么。

面试中我们会请你讲一个具体的排查过程，
重点不是结果，而是你如何缩小范围、
如何区分环境问题与代码问题、
以及在信息不足时怎么决定下一步查什么。

## 关于团队氛围

我们不追求技术栈新潮，更在意把已有的东西用对。
评审时欢迎反对意见，但要求给出替代方案与代价，
而不是只说我觉得这样不好。

## 关于成长路径

入职后前三个月会有明确的带教安排：
先跟着改几个小需求熟悉链路，
再独立负责一个完整模块。
我们不设必须满几年才能碰核心链路的门槛，
但要求改动前把影响面说清楚。

半年后通常会有一次方向选择：
是继续深入检索与推理链路，
还是转向数据管道与平台工程。
两条路都有对应的负责人带教。

## 日常工作节奏

每天有一次十五分钟的站会，只同步进度与阻塞，
不做技术讨论，技术讨论会后单独约。

代码合并前必须有至少一人评审通过，
关键链路（检索、计费）需要两人评审。
评审意见必须在合并前处理完或明确记录为后续项。

每周五下午是自主时间，
可以用于偿还技术债、读源码或写文档。
我们更希望这段时间被用来做长期有价值但短期没产出的工作。

## 我们如何衡量工程师

不看代码行数，也不看提交次数。
主要看三件事：交付的功能是否稳定、
遇到问题时的排查过程是否清晰、
以及是否把经验沉淀成了别人能用的东西。

第三点最容易被忽略，但长期看它决定团队的复利。
一个人的排查经验如果只留在自己脑子里，
下次同样的问题换个人还要重来一遍。


## 我们如何评估候选人

不考背诵式的八股。会请你讲一个你真正做过的项目，
然后针对其中某个技术选择追问三层：
为什么选它、代价是什么、如果重来会怎么改。

也会给一个具体的故障现象，请你说排查思路。
我们关注的不是你能不能立刻给出答案，
而是你**如何缩小范围、如何排除可能性**。

如果某个问题你确实不知道，直说比猜要好。
我们会换一个方向继续，而不是记一笔"不会"。


## 关于技术债

我们不追求零技术债，那既不现实也没必要。
但要求技术债是**有记录的**：知道它在哪里、
为什么先欠着、以及什么条件下该还。

没有记录的技术债才是真问题，
因为它会在某次改动中突然变成阻塞，
而那时没人记得它是怎么来的。

## 关于文档

文档不是写给评审看的，是写给三个月后的自己看的。
因此我们把为什么放在怎么做前面 ——
代码能说明怎么做，但说不清当初排除了哪些方案。

本岗位需要写的文档主要是三类：
技术方案（含取舍与回滚方案）、
故障复盘（含根因与预防措施）、
以及接口与数据模型说明。
"""


PROJECT_MATERIAL = """# 智能客服知识库系统 —— 项目说明

## 项目背景

公司原有客服系统依赖人工整理常见问题文档，存在三个问题：
新人上手慢、同一问题的答案在不同客服之间不一致、
产品迭代后文档更新滞后导致回答过期。

本项目把历史工单、产品手册与常见问题统一处理为可检索的知识库，
让客服在对话过程中实时获得有出处的答案建议，
并在无法确定时明确转人工，而不是编造答案。

## 系统架构

整体分为四层：接入层、检索层、生成层与数据层。

接入层用反向代理做负载均衡，后端提供 HTTP 接口，
接口层只做参数校验与鉴权，业务逻辑下沉到服务层。

检索层是核心，采用双路召回。
稠密向量召回负责语义相似：用户问扣款失败怎么办，
能召回标题是支付异常处理的文档。
稀疏关键词召回负责术语与产品型号的精确匹配：
用户输入具体型号时，纯语义检索经常匹配到语义相近
但型号不同的文档。

两路结果用倒数排名融合的方式合并后，交给重排模型精排。
这种融合只用排名而不依赖分数尺度，
因此避免了两种分数不可比的问题，这也是我们当初不选加权求和的原因。

生成层调用大模型，把重排后的片段作为上下文拼接进提示词，
并要求模型在回答中标注引用的片段编号，便于客服核对答案出处。

数据层用 PostgreSQL 存原始工单与元数据，
用 Milvus 存向量，用 Redis 缓存高频问题的检索结果，
缓存有效期设为三十分钟：太短则命中率低，
太长则产品更新后答案过期。

## 关键技术决策

### 切块策略：从定长改成语义边界

早期用固定长度切分，发现产品手册里的表格和步骤列表
经常被从中间截断，导致检索到的片段缺少关键参数。
例如退款时限表格被切开后，召回片段只有表头没有数值。

修法是先按标题层级切分，再对超长段落做定长切分，
并保留重叠窗口以避免边界处的句子被割裂。
这个改动让表格类问题的回答准确率明显提升。

### 为什么必须引入重排

双路召回把候选集扩大到五十条，但前五条的准确率反而下降了，
因为融合后的排序只看排名，没有考虑查询与片段的细粒度相关性。

加入交叉编码器重排后，前五条的准确率提升明显。
代价是每次查询多约八十毫秒，我们认为这个延迟可以接受。

### 拒答阈值：不是所有问题都能答

统计了无法回答的问题的相似度分布，据此设定阈值。
低于阈值时系统会拒答并转人工，避免编造。
这个取舍牺牲了一部分召回，换来的是准确率，
因为客服对答错的容忍度远低于答不出。

## 评估与效果

建立了二百条标注的评估集，覆盖产品咨询、故障排查、
账单问题三类，每类都有明确的正确片段标注。

用前五条召回率与平均倒数排名作为主指标，
每次改动都在评估集上回归，不通过就不合并。
这套流程拦住过两次感觉变好了但指标变差的改动，
其中一次是把切块粒度从五百改到三百，主观上觉得更精确，
但召回率掉了四个百分点。

上线后人工转接率下降百分之三十四，平均响应时间从四十五秒降到八秒。

## 踩过的坑

第一个坑是向量维度不一致导致插入失败。
早期换过一次嵌入模型，新模型是一千五百三十六维
而集合是固定的一千零二十四维，插入直接报错。
这个还算好查，因为报错很明确。更麻烦的是如果当时没报错、
只是写进去了错误的向量，那检索结果会静默变差。

第二个坑是删除文档时留下了孤儿向量。
删除文档只删了数据库记录，向量库里的向量没删。
后果是检索时仍会命中这些向量，但回表取不到正文，
代码里是跳过处理，所以不报错，只是实际召回数变少了，
表现为检索质量下降。这个问题潜伏了很久，
因为指标是缓慢变化的，没有突变点。

第三个坑是默认一致性级别下的间歇性空召回。
上传后立刻检索偶发命中零段。
这个问题最麻烦的地方是间歇性：本地复现不出来，
线上偶尔出现，日志里没有任何异常。

## 排查方法上的收获

这三个坑的共同点是失败是静默的：不报错、不崩溃，只是结果变差。
因此后来我们加了两件事。

一是状态一致性检查脚本，定期比对数据库与向量库的实际状态，
发现不一致就报出来。第二个和第三个坑都是靠这类检查暴露的。

二是把不变量写成断言。例如文档标为已索引后，
它的每个片段必须能在向量库里查到。
这条断言直接把第三个坑从偶发怪象变成了一条可验证的规则。

经验是：能自动发现的失败，就不要靠人去注意。

## 遗留问题

一是重排模型用的是通用交叉编码器，没有针对客服场景微调，
在口语化提问上的表现一般。

二是缓存失效策略是按时间，没有跟产品文档的更新事件联动，
因此文档更新后最长会有三十分钟的答案滞后。
我们讨论过改成事件驱动失效，但优先级一直排不上去。


## 成本与容量

向量存储的成本主要来自内存占用。
一千零二十四维的向量，单条约四 KB，
一百万条约四 GB，加上索引开销实际约六到八 GB。
这个量级用单机部署可以承受，
因此我们初期没有上分布式方案，
而是先用容量评估确定增长曲线后再决定。

嵌入调用是另一项成本，按文本量计费。
我们把切块大小从三百改回五百，一部分原因就是
更小的块意味着更多的调用次数。
这个成本考量与召回质量之间存在张力，
最终是按评估集上的净收益决定的。

## 协作与分工

项目由三人承担：一名后端负责检索链路与数据管道，
一名算法负责重排模型与阈值调参，
一名前端负责客服工作台界面。

我负责检索链路部分，包括双路召回的实现、
融合策略的选型与评估集的搭建。
评估集的标注由全体客服参与，每人标注约五十条，
交叉复核后再作为基线使用。


## 成本与容量

向量存储的成本主要来自内存占用。
一千零二十四维的向量，单条约四 KB，
一百万条约四 GB，加上索引开销实际约六到八 GB。
这个量级用单机部署可以承受，
因此我们初期没有上分布式方案，
而是先用容量评估确定增长曲线后再决定。

嵌入调用是另一项成本，按文本量计费。
我们把切块大小从三百改回五百，一部分原因就是
更小的块意味着更多的调用次数。
这个成本考量与召回质量之间存在张力，
最终是按评估集上的净收益决定的。

## 协作与分工

项目由三人承担：一名后端负责检索链路与数据管道，
一名算法负责重排模型与阈值调参，
一名前端负责客服工作台界面。

我负责检索链路部分，包括双路召回的实现、
融合策略的选型与评估集的搭建。
评估集的标注由全体客服参与，每人标注约五十条，
交叉复核后再作为基线使用。

## 后续规划

下一步打算做三件事。
一是把重排模型换成针对客服语料微调过的版本，
这项预计能再提升几个点的前五条准确率。

二是把缓存失效改成事件驱动，
产品文档更新时主动清理相关缓存。

三是把评估集从二百条扩充到五百条，
并补充多轮对话场景的标注 ——
当前的评估集只覆盖单轮提问，
而实际使用中客服经常追问。
"""


CODE_MATERIAL = """# retrieval_service.py —— 检索服务实现

```python
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.document_chunk import DocumentChunk
from app.services.milvus_vector_store import MilvusVectorStore
from app.services.openai_embedding_service import OpenAIEmbeddingService


class RetrievalService:
    # 按项目与资料类型过滤的向量检索。

    def __init__(self, db: AsyncSession):
        self.db = db
        self.embedding_service = OpenAIEmbeddingService()
        self.vector_store = MilvusVectorStore()

    async def retrieve(
        self,
        query: str,
        limit: int = 5,
        project_id: int | None = None,
        document_type: str | None = None,
    ) -> list[dict]:
        if not query.strip():
            raise ValueError("检索问题不能为空")

        # 第一步：把查询嵌入成向量
        query_vector = await self.embedding_service.embed_text(query)

        # 第二步：组装过滤表达式
        filters = []
        if project_id is not None:
            filters.append(f"project_id == {project_id}")
        if document_type is not None:
            filters.append(f"document_type == {document_type}")
        filter_expression = " 与 ".join(filters) or None

        # 第三步：向量检索
        results = await self.vector_store.search(
            vector=query_vector,
            limit=limit,
            filters=filter_expression,
        )
        if not results:
            return []

        # 第四步：按标识回表取正文
        chunk_ids = [item["id"] for item in results]
        db_result = await self.db.execute(
            select(DocumentChunk).where(DocumentChunk.id.in_(chunk_ids))
        )
        chunks = {c.id: c for c in db_result.scalars().all()}

        # 第五步：组装结果，跳过已被删除的片段
        retrieved = []
        for item in results:
            chunk = chunks.get(item["id"])
            if not chunk:
                continue
            retrieved.append({
                "chunk_id": chunk.id,
                "document_id": chunk.document_id,
                "content": chunk.content,
                "score": item["distance"],
            })
        return retrieved
```

## 为什么这个服务保持得很薄

该服务刻意不做重排、不做查询改写、不做阈值过滤。

这些能力如果将来引入，应该在独立的环节实现，
而不是混进检索服务。理由是评估时必须能分清
是哪一步带来的变化。若把重排混进检索方法，
那么召回指标提升了也说不清是召回变好还是排序变好，
后续调参就没有依据。

## 一致性级别必须设为强一致

向量库默认的默认级别不保证插入后立即可检索，
而上传流程在插入后立刻把文档标记为已索引，
调用方紧接着就会检索。

实测两种级别下的差异非常明确：默认级别时
插入后立刻检索十次命中零次，换成强一致后十次全部命中。
这个缺陷的表现是上传成功但面试只出通用题，
而且是间歇性的，属于最难排查的一类问题，
因为它看起来像模型在随机发挥。

## 关于分数字段

分数直接透传向量库返回的余弦距离，没有做任何归一化，
因为当前只有单路召回，不存在两路分数需要对齐。

如果将来引入稀疏召回做混合检索，两路分数的量级差异
就必须处理：可以用极差归一化、标准分归一化，
或者用只用排名的融合方式。
届时这个选择应该由评估集上的实测结果决定，
而不是先验假定，因为两种方法对离群值的敏感度
和对分布形状的假设都不一样。

## 已知局限

一、单路稠密检索对精确术语的匹配弱于语义匹配，
用户输入具体型号或函数名时容易召回语义相近但内容不对的片段。

二、切块是定长字符切分，会把表格与代码块从中间截断。

三、没有相似度阈值过滤，低分片段也会进入上下文，
大模型可能拿它当依据来编造。

四、没有查询改写，用户的口语化提问直接拿去检索。

这些局限都有对应的改进方向，但每一项都需要
先在评估集上量化现状，否则无法判断改动是变好还是变坏。
"""


DOCUMENTS: tuple[EvaluationDocument, ...] = (
    EvaluationDocument(
        name="zhangming_resume.md",
        document_type="resume",
        content=RESUME,
    ),
    EvaluationDocument(
        name="senior_backend_jd.md",
        document_type="jd",
        content=JOB_DESCRIPTION,
    ),
    EvaluationDocument(
        name="knowledge_base_project.md",
        document_type="project",
        content=PROJECT_MATERIAL,
    ),
    EvaluationDocument(
        name="retrieval_service_code.md",
        document_type="code",
        content=CODE_MATERIAL,
    ),
)


@dataclass(frozen=True)
class EvaluationCase:
    """一个检索评估用例。

    `expected_marker` 是一段落在**单个块内**的特征子串。
    校验脚本会断言这一点 —— 跨块时 chunk 级指标没有明确含义。
    """

    id: str
    query: str
    document_name: str
    expected_marker: str
    document_type: str | None = None
    difficulty: str = "easy"


EVALUATION_CASES: tuple[EvaluationCase, ...] = (
    EvaluationCase(
        id="resume_async_atomicity",
        query="异步任务领取为什么要保证原子性？",
        document_name="zhangming_resume.md",
        expected_marker="为此写下一条不变量：任务领取必须是单条原子语",
        document_type='resume',
        difficulty="hard",
    ),
    EvaluationCase(
        id="resume_consistency_evidence",
        query="向量库一致性级别的问题实测数据是什么？",
        document_name="zhangming_resume.md",
        expected_marker="紧接着就会检索。实测默认级别下插入后立刻检索",
        document_type='resume',
        difficulty="medium",
    ),
    EvaluationCase(
        id="resume_order_latency",
        query="订单重构之后的延迟指标是多少？",
        document_name="zhangming_resume.md",
        expected_marker="重构后订单接口的尾部延迟指标从一千二百毫秒降",
        document_type='resume',
        difficulty="medium",
    ),
    EvaluationCase(
        id="resume_programming_languages",
        query="候选人主力语言是哪个，用了多久？",
        document_name="zhangming_resume.md",
        expected_marker="Python 3.13 为主力语言，累计约四",
        document_type='resume',
        difficulty="easy",
    ),
    EvaluationCase(
        id="resume_expected_city",
        query="候选人期望在哪些城市工作？",
        document_name="zhangming_resume.md",
        expected_marker="求职方向：后端开发与 AI 应用开发，期望工",
        document_type='resume',
        difficulty="easy",
    ),
    EvaluationCase(
        id="resume_weakness",
        query="候选人自认不擅长什么？",
        document_name="zhangming_resume.md",
        expected_marker="我不擅长的是纯前端视觉设计，能实现但要靠设计",
        document_type='resume',
        difficulty="easy",
    ),
    EvaluationCase(
        id="jd_vector_db",
        query="岗位要求什么向量数据库经验？",
        document_name="senior_backend_jd.md",
        expected_marker="四、有向量数据库的实际使用经验，理解向量索引",
        document_type='jd',
        difficulty="easy",
    ),
    EvaluationCase(
        id="jd_evaluation_bonus",
        query="什么样的评估经验算加分项？",
        document_name="senior_backend_jd.md",
        # ⚠️ 这里原来写的是"良好的沟通能力，能向非技术角色解释技术取舍。"
        # —— 那是「任职要求 六」，**不回答"加分项"这个问题**。
        #
        # 它只是"恰好出现在某个块里"，而 `resolve_expectations`
        # 用的是纯子串匹配，于是那个块被当成了"正确答案"。
        # 后果：**指标的分子从一开始就是错的** ——
        # 所有基于它的对比（包括"重排提升了多少"）都不可信。
        #
        # 语料里真正的加分项是"有检索增强系统的检索质量评估经验，
        # 能用量化指标驱动优化，而不是凭主观判断感觉变好了"。
        #
        # ⚠️ marker 取的是**后半句**，不是整句。
        # 整句从第 489 字符起，而校验用的切块（无重叠、500 字符）
        # 恰好在第 500 字符断开 —— 整句**跨块**，而跨块时
        # "命中哪一块算对"没有定义（`check_eval_dataset` 会断言这点）。
        # 可用区间只有 [500, 517) —— 前一句在 500 处被切断，
        # 后一句在 517 处换行。因此 marker 从 500 起取
        # （以逗号结尾但仍表明"评估经验"这个要点），共 17 字。
        #
        # ⚠️ 不能包含换行：语料在"优化，"之后换行，
        # 带上换行的串会找不到（实测报"marker 在材料里找不到"）。
        expected_marker="量评估经验，能用量化指标驱动优化",
        document_type='jd',
        difficulty="medium",
    ),
    EvaluationCase(
        id="jd_stuck_task",
        query="加分项里提到的任务卡住是指什么？",
        document_name="senior_backend_jd.md",
        expected_marker="熟悉分布式追踪等可观测性体系，有排查线上问题",
        document_type='jd',
        difficulty="medium",
    ),
    EvaluationCase(
        id="jd_interview_rounds",
        query="技术面试一共几轮，怎么安排的？",
        document_name="senior_backend_jd.md",
        expected_marker="复面一小时以系统设计为主，最后一轮是三十分钟",
        document_type='jd',
        difficulty="easy",
    ),
    EvaluationCase(
        id="jd_team_size",
        query="团队多少人，负责什么方向？",
        document_name="senior_backend_jd.md",
        expected_marker="团队规模为八人，负责公司智能中台的能力建设，",
        document_type='jd',
        difficulty="easy",
    ),
    EvaluationCase(
        id="project_rrf_choice",
        query="两路召回为什么用倒数排名融合而不是加权求和？",
        document_name="knowledge_base_project.md",
        expected_marker="倒数排名融合的方式合并后，交给重排模型精排。",
        document_type='project',
        difficulty="hard",
    ),
    EvaluationCase(
        id="project_chunking_fix",
        query="切块被截断的问题怎么修的？",
        document_name="knowledge_base_project.md",
        expected_marker="修法是先按标题层级切分，再对超长段落做定长切",
        document_type='project',
        difficulty="medium",
    ),
    EvaluationCase(
        id="project_refusal_tradeoff",
        query="拒答阈值这个取舍是怎么权衡的？",
        document_name="knowledge_base_project.md",
        expected_marker="建立了二百条标注的评估集，覆盖产品咨询、故障",
        document_type='project',
        difficulty="hard",
    ),
    EvaluationCase(
        id="project_rejected_change",
        query="有没有改动被评估集拦下来过？",
        document_name="knowledge_base_project.md",
        expected_marker="把切块粒度从五百改到三百，主观上觉得更精确，",
        document_type='project',
        difficulty="medium",
    ),
    EvaluationCase(
        id="project_orphan_vectors",
        query="孤儿向量是怎么产生的，为什么难发现？",
        document_name="knowledge_base_project.md",
        expected_marker="跳过处理，所以不报错，只是实际召回数变少了，",
        document_type='project',
        difficulty="hard",
    ),
    EvaluationCase(
        id="project_lessons",
        query="从这些坑里总结出的经验是什么？",
        document_name="knowledge_base_project.md",
        expected_marker="经验是：能自动发现的失败，就不要靠人去注意。",
        document_type='project',
        difficulty="easy",
    ),
    EvaluationCase(
        id="code_filter_expression",
        query="过滤表达式是怎么拼出来的？",
        document_name="retrieval_service_code.md",
        expected_marker="vice.embed_text(query)",
        document_type='code',
        difficulty="easy",
    ),
    EvaluationCase(
        id="code_skip_deleted",
        query="检索到已经被删除的片段怎么处理？",
        document_name="retrieval_service_code.md",
        expected_marker="        # 第五步：组装结果，跳过已",
        document_type='code',
        difficulty="easy",
    ),
    EvaluationCase(
        id="code_thin_reason",
        query="为什么不让检索服务自己做重排和阈值过滤？",
        document_name="retrieval_service_code.md",
        expected_marker="那么召回指标提升了也说不清是召回变好还是排序",
        document_type='code',
        difficulty="medium",
    ),
    EvaluationCase(
        id="code_normalization_future",
        query="将来做混合检索时分数归一化要注意什么？",
        document_name="retrieval_service_code.md",
        expected_marker="就必须处理：可以用极差归一化、标准分归一化，",
        document_type='code',
        difficulty="hard",
    ),
    EvaluationCase(
        id="code_limitations",
        query="已知局限里列了哪几条？",
        document_name="retrieval_service_code.md",
        expected_marker="四、没有查询改写，用户的口语化提问直接拿去检",
        document_type='code',
        difficulty="easy",
    ),
    EvaluationCase(
        id="cross_strong_consistency",
        query="强一致性和十次命中的实测结论出自哪里？",
        document_name="retrieval_service_code.md",
        expected_marker="插入后立刻检索十次命中零次，换成强一致后十次",
        document_type=None,
        difficulty="medium",
    ),
)
