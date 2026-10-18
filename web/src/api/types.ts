/**
 * 与后端契约对应的类型。
 *
 * 字段**照抄后端** `app/schemas/` 与路由的响应构造，
 * 不做"前端更顺手"的重命名 —— 一旦两套名字并存，
 * 后端改字段时前端不会报错，只会在运行时静默拿到 undefined。
 */

export interface User {
  id: number
  username: string
  email: string
}

export interface TokenResponse {
  access_token: string
  token_type: string
}

export interface Project {
  id: number
  name: string
  description: string | null
  owner_id: number
  status: string
  created_at: string
  updated_at: string
}

/** 面试会话的状态机取值（见 §10）。 */
export type SessionStatus =
  | 'draft'
  | 'preparing_context'
  | 'planned'
  | 'asking'
  | 'waiting_for_answer'
  | 'evaluating'
  | 'summarizing'
  | 'completed'
  | 'paused'
  | 'cancelled'
  | 'failed'
  // 迁移前的旧值，后端仍会读出来（LEGACY_STATUS_MAP）
  | 'created'

export interface InterviewSession {
  id: number
  user_id: number
  project_id: number
  status: SessionStatus
  interview_type: string
  target_role: string | null
  current_question_index: number

  /** 单场题目预算上限（含追问）。 */
  max_questions: number

  /** 本场已问数量（含追问）。 */
  questions_asked: number

  /** 结束原因，取值见后端的 TerminationReason。 */
  termination_reason: string | null

  /** 暂停前所在状态；恢复时回到它。 */
  resume_status: string | null
  pause_reason: string | null
  paused_at: string | null

  created_at: string
  updated_at: string
}

/**
 * 某一轮作答的逐轮分析留档。
 *
 * 只保留**复盘时真正要看**的东西：漏了什么、三个维度的档位。
 * 完整分析（原始提示词、模型输出）不存 —— 体积大且无复盘价值。
 */
export interface AnswerAnalysisSnapshot {
  /** 这一轮回答被判定为缺失的点。 */
  missing_points: string[]
  /**
   * 三个维度的档位（A/B/C/D）。
   *
   * 键名来自后端 `anchor_summary()`，例如
   * `answer_quality` / `technical_depth` / `completeness`。
   */
  anchors: Record<string, string | null>
  /** 采样次数。线上默认 1 次（延迟优先）。 */
  sample_count: number | null
}

export interface InterviewQuestion {
  id: number
  session_id: number
  question: string
  question_type: string
  question_index: number
  context: string | null

  /** 资料依据。为空表示这是通用能力题（`is_general` 为 true）。 */
  evidence_chunk_ids: number[]
  is_general: boolean

  created_at: string

  /** 该题已有的回答（未作答为 null）。 */
  answer: string | null
  answered_at: string | null

  /**
   * 逐轮分析留档（复盘用）。
   *
   * `null` 有两种含义：还没作答，或记录早于该功能（历史数据）。
   * 前端一律**隐藏该区块** —— 对用户来说两者结果相同，
   * 而显示"无数据"会让人以为坏了。
   */
  analysis: AnswerAnalysisSnapshot | null
}

export interface InterviewEvaluation {
  id: number
  overall_score: number
  technical_score: number
  project_score: number
  communication_score: number
  strengths: string[]
  weaknesses: string[]
  suggestions: string[]
  feedback: string
  scoring_details?: Record<string, unknown>
  created_at?: string
}

export interface InterviewDetail {
  session: InterviewSession
  questions: InterviewQuestion[]
  evaluation: InterviewEvaluation | null
  /** 当前允许转移到的状态，由服务端计算（前端不重实现状态机）。 */
  allowed_transitions: string[]
}

export interface InterviewStartResult {
  session_status: SessionStatus
  current_question_index: number
  question: InterviewQuestion
}

/** 一条资料依据（面试问题所依据的资料片段）。 */
export interface InterviewEvidenceItem {
  chunk_id: number

  /** 片段正文（**完整**返回，服务端不截断）。 */
  content: string

  /** 来源文件，让用户认出"这是我的哪份资料"。 */
  document_id: number | null
  document_name: string | null
  document_type: string | null
}

export interface InterviewEvidence {
  question_id: number
  question: string
  is_general: boolean

  /** 按 `evidence_chunk_ids` 的原顺序返回。 */
  items: InterviewEvidenceItem[]

  /** 依据已失效（资料被删除）的 chunk id。 */
  missing_chunk_ids: number[]
}

/** 历史列表里的一条会话。 */
export interface InterviewSessionListItem extends InterviewSession {
  /** 项目名，由服务端按 id 批量补上。 */
  project_name: string | null

  /**
   * 已作答的题数。
   *
   * 注意与 `questions_asked` 的区别：后者是"问了几题"。
   * 用户可能看到题就关了，因此列表显示已作答数才有意义。
   */
  answered_count: number
}

export interface InterviewSessionListResponse {
  items: InterviewSessionListItem[]
  total: number
  limit: number
  offset: number
}

/** 单个维度的能力画像。 */
export interface DimensionProfile {
  key: string
  label: string
  count: number

  /** 中位数（服务端不用平均值，见能力画像服务说明）。 */
  median: number | null

  minimum: number | null
  maximum: number | null
  latest: number | null

  /** 极差。本项目一直用这个口径衡量波动（§8A.9）。 */
  spread: number | null

  /** 逐次分数，**按时间正序**（旧 → 新），用于画趋势。 */
  history: number[]
}

export interface RecurringWeakness {
  text: string
  occurrences: number
}

export interface ProfileSession {
  session_id: number
  project_id: number
  project_name: string | null
  target_role: string | null
  evaluated_at: string
  scores: Record<string, number>
}

export interface AbilityProfile {
  session_count: number
  dimensions: DimensionProfile[]
  sessions: ProfileSession[]
  recent_weaknesses: string[]
  recent_suggestions: string[]
  recurring_weaknesses: RecurringWeakness[]

  /** 样本是否足以支撑"优势 / 短板"判断。 */
  sufficient_samples: boolean

  /** 采样与解读说明 —— 必须展示，否则用户会把波动当进步。 */
  caveats: string[]
}

export interface ListInterviewsParams {
  /** 只看还没结束的面试 —— 用于「找回没做完的」。 */
  unfinished_only?: boolean
  project_id?: number
  limit?: number
  offset?: number
}

export interface AnswerAnalysisSummary {
  sample_count: number
  missing_points_count: number
  anchors: Record<string, string | null>
}

export interface InterviewAnswerResult {
  answer_id: number
  question_id: number
  answer: string
  answered_at: string

  /** 为 null 表示面试已结束（题目预算耗尽）。 */
  follow_up_question: InterviewQuestion | null

  session_status: SessionStatus

  /** 是否因预算耗尽自动结束了面试。 */
  finished: boolean
  termination_reason: string | null

  /** 自动结束时带回的整场评价。 */
  evaluation: InterviewEvaluation | null

  analysis_summary: AnswerAnalysisSummary | null
}

export interface InterviewFinishResult {
  session_status: SessionStatus
  evaluation: InterviewEvaluation
}

/** 文档可选的类型，与后端 `document_type` 的字面量一致。 */
export type DocumentType =
  | 'resume'
  | 'jd'
  | 'project'
  | 'code'
  | 'other'

/**
 * 文档状态。
 *
 * `chunked` 是**关键状态**：文本已切块但还没写入向量索引，
 * 此时检索不到它，面试只会出通用题。
 * 只有 `embedded` 才真正可被检索。
 */
export type DocumentStatus =
  | 'pending'
  | 'parsed'
  | 'chunked'
  | 'embedded'
  | 'failed'

export interface DocumentItem {
  id: number
  name: string
  original_filename: string
  file_type: string
  document_type: string
  project_id: number
  status: DocumentStatus
  /** 后端会返回全文；列表页不需要，前端不渲染它。 */
  content: string | null
  created_at: string
}

/** 状态轨迹的一条记录（§10.7）。 */
export interface StatusHistoryEntry {
  id: number
  from_status: string
  to_status: string
  trigger: string
  created_at: string
}

export interface UsageMetricUsage {
  metric: string
  used: number
  quota: number
  remaining: number
}

/**
 * 开一场面试的成本预估。
 *
 * ## 为什么它和 `allowed` 是两个不同的问题
 *
 * `allowed` 回答"**能不能开始**"，而它回答"**能不能开完**"。
 *
 * 实测陷阱：剩余 2 场、8 题时把题目上限设成 8 并开始 ——
 * 第 3 场能开，但**第 9 题会被拒**，面试在中间断掉。
 * 而那时场次已经花掉了，用户既没得到完整面试，也拿不回场次。
 *
 * 所以真正需要提前告知的是 `insufficient_questions` 这一种。
 */
export interface InterviewCostPreview {
  /** 规范化之后的题目预算（用户可能设了越界值，服务端会夹取） */
  questions: number
  /** 这一场需要的场次（恒为 1） */
  interviews: number
  /** 是否够开完 */
  fits: boolean
  /**
   * 不够时的原因。
   *
   * ⚠️ 值必须与后端 `interview_cost.estimate_cost` 返回的**完全一致**。
   * 我第一版在这里写成 `'interviews' | 'questions'`，
   * 而后端实际返回 `insufficient_interviews` / `insufficient_questions` ——
   * 那样前端的所有判断都会**静默落空**（TypeScript 也发现不了，
   * 因为两边都是合法字符串类型）。
   *
   * 这类"两端拼写不一致"是接口最常见的隐性缺陷之一。
   */
  blocked_by: 'insufficient_interviews' | 'insufficient_questions' | null
  /** 会断在第几题（仅 `insufficient_questions` 时有值） */
  truncates_at: number | null
  /** 给用户看的一句话。**文案由服务端给** —— 它依赖规范化后的预算 */
  message: string
}

/**
 * 额度视图。
 *
 * 后端**成对**返回场次与题目两份额度：任一先耗尽都会挡住用户，
 * 只显示"剩余 N 场"会让人以为还能用（§25.5）。
 * `limited_by` 由服务端给出，前端不要自己比较两个剩余量。
 */
export interface Usage {
  period: string
  interviews: UsageMetricUsage
  questions: UsageMetricUsage
  limited_by: 'interviews' | 'questions' | null
  allowed: boolean
  cost_preview?: InterviewCostPreview | null
}
