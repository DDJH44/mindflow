/**
 * 各域接口封装。
 *
 * 一个域一个文件，只做"路径 + 方法 + 类型"的映射，
 * 不放业务判断 —— 状态机是否允许某操作由后端决定（§10），
 * 前端只负责展示 `allowed_transitions`。
 */

import { http } from './http'
import type {
  AbilityProfile,
  DocumentItem,
  DocumentType,
  FocusSuggestion,
  InterviewAnswerResult,
  InterviewDetail,
  InterviewEvaluation,
  InterviewEvidence,
  InterviewFinishResult,
  InterviewQuestion,
  InterviewSession,
  InterviewSessionListResponse,
  InterviewStartResult,
  ListInterviewsParams,
  NextStep,
  Project,
  ProjectReadiness,
  StatusHistoryEntry,
  TokenResponse,
  Usage,
  User,
} from './types'

// ---------------------------------------------------------------- 认证

export const authApi = {
  async login(account: string, password: string) {
    const { data } = await http.post<TokenResponse>('/auth/login', {
      account,
      password,
    })
    return data
  },

  async register(
    username: string,
    email: string,
    password: string,
    inviteCode?: string,
  ) {
    const { data } = await http.post<{ message: string; user: User }>(
      '/auth/register',
      {
        username,
        email,
        password,
        // 只在有值时发送：留空字符串会让后端把"空"当成"用户填了个空码"，
        // 于是报"邀请码无效"而不是"缺少邀请码" —— 后者才是真实原因。
        ...(inviteCode ? { invite_code: inviteCode } : {}),
      },
    )
    return data
  },

  /**
   * 注册是否需要邀请码。
   *
   * 登录页据此决定要不要显示邀请码输入框。
   * 不在前端写死"总是显示"：本地开发时会多一个没用的必填项；
   * 也不写死"从不显示"：开启邀请制后用户不知道要填什么，
   * 而失败原因看起来像"服务器坏了"。
   */
  async registrationMode() {
    const { data } = await http.get<{ require_invite: boolean }>(
      '/auth/registration-mode',
    )
    return data
  },

  async me() {
    const { data } = await http.get<User>('/auth/me')
    return data
  },
}

// ---------------------------------------------------------------- 项目

export const projectApi = {
  async list() {
    const { data } = await http.get<Project[]>('/projects')
    return data
  },

  async create(name: string, description?: string) {
    const { data } = await http.post<Project>('/projects', {
      name,
      description,
    })
    return data
  },

  async detail(id: number) {
    const { data } = await http.get<Project>(`/projects/${id}`)
    return data
  },

  /**
   * 资料体检：哪些类型已索引、缺什么、面试会用哪些。
   *
   * 面试只检索四类材料且只检索已索引的，而这两点用户看不到 ——
   * 于是"面试老问简历"会被理解成"面试质量不行"。
   */
  async readiness(id: number) {
    const { data } = await http.get<ProjectReadiness>(
      `/projects/${id}/readiness`,
    )
    return data
  },
}

// ---------------------------------------------------------------- 资料

export const documentApi = {
  async list(projectId: number) {
    const { data } = await http.get<DocumentItem[]>(
      `/projects/${projectId}/documents`,
    )
    return data
  },

  /**
   * 上传文档。
   *
   * `document_type` 走 query 而不是 body：后端的签名是
   * `file: UploadFile = File(...)` 加一个裸标量参数，
   * FastAPI 会把后者当 query 参数。
   * 放进 FormData 会被忽略、静默退化为默认值 `other`。
   */
  async upload(
    projectId: number,
    file: File,
    documentType: DocumentType = 'other',
    onProgress?: (percent: number) => void,
  ) {
    const form = new FormData()
    form.append('file', file)

    const { data } = await http.post<DocumentItem>(
      `/projects/${projectId}/documents`,
      form,
      {
        params: { document_type: documentType },
        // 上传含解析 + 切块 + 嵌入（逐块调嵌入服务），
        // 大简历可能需要好几分钟，因此不设全局超时。
        timeout: 0,
        onUploadProgress: (event) => {
          if (!onProgress || !event.total) {
            return
          }
          onProgress(
            Math.round((event.loaded / event.total) * 100),
          )
        },
      },
    )

    return data
  },

  /** 重试向量索引（幂等）。文档停在 `chunked` 时用它。 */
  async embed(projectId: number, documentId: number) {
    const { data } = await http.post<DocumentItem>(
      `/projects/${projectId}/documents/${documentId}/embed`,
      null,
      { timeout: 0 },
    )
    return data
  },

  /**
   * 删除文档。
   *
   * 后端会**同时**删掉 Milvus 里的向量 —— 漏删会留下孤儿向量，
   * 检索时占用 top-k 名额却取不到正文，静默降低召回。
   */
  async remove(projectId: number, documentId: number) {
    await http.delete(
      `/projects/${projectId}/documents/${documentId}`,
    )
  },
}

// ---------------------------------------------------------------- 额度

export const usageApi = {
  /**
   * 读当前账期的额度与成本预估。
   *
   * `maxQuestions` 传进来时，服务端按那个题目预算预估 ——
   * 用户改了"题目数量上限"后前端应带它重新请求，
   * 这样"能不能开完"的判定才跟着表单变。
   *
   * 判定逻辑**不复制到前端**：规范化（夹取越界值）与
   * "断在第几题"的计算都依赖服务端的常量，
   * 前端各算一套迟早会与后端不一致。
   */
  async current(maxQuestions?: number | null) {
    const { data } = await http.get<Usage>('/usage', {
      params:
        maxQuestions === undefined || maxQuestions === null
          ? undefined
          : { max_questions: maxQuestions },
    })
    return data
  },
}

// ---------------------------------------------------------------- 面试

export interface CreateSessionPayload {
  project_id: number
  interview_type?: string
  target_role?: string | null
  max_questions?: number | null
}

export const interviewApi = {
  /**
   * 列出当前用户的面试会话（最近更新的在前）。
   *
   * `unfinished_only` 是历史页最有用的筛选：用户打开历史时
   * 首先想知道"有没有还没做完的面试"。
   */
  async list(params: ListInterviewsParams = {}) {
    const { data } = await http.get<InterviewSessionListResponse>(
      '/interviews',
      { params },
    )
    return data
  },

  /**
   * 能力画像（跨场次聚合）。
   *
   * 路径不接受 `user_id`：画像永远只看自己。
   */
  async abilityProfile(projectId?: number) {
    const { data } = await http.get<AbilityProfile>(
      '/interviews/profile/ability',
      { params: projectId ? { project_id: projectId } : {} },
    )
    return data
  },

  /**
   * 「下一步做什么」—— 闭环的入口。
   *
   * 后端只给**一条**建议（给三条等于没有建议）。
   * 判断逻辑全在服务端（含"样本少不下短板结论"这条原则），
   * 前端不要自己再推一遍 —— 那会让两边迟早不一致。
   */
  async nextStep(projectId?: number) {
    const { data } = await http.get<NextStep>(
      '/interviews/profile/next-step',
      { params: projectId ? { project_id: projectId } : {} },
    )
    return data
  },

  async create(payload: CreateSessionPayload) {
    const { data } = await http.post<InterviewSession>(
      '/interviews',
      payload,
    )
    return data
  },

  async detail(id: number) {
    const { data } = await http.get<InterviewDetail>(
      `/interviews/${id}/detail`,
    )
    return data
  },

  /**
   * **这一场暴露了什么** → 下一场的聚焦方向（报告页内联 CTA）。
   *
   * 单场内容回顾，不受"样本少不下结论"约束 —— 判断在服务端
   * （`build_focus_from_report`），前端拿到 `query` 直接传给
   * `start` 即可。无评价时 `query` 为 null。
   */
  async focusSuggestion(id: number) {
    const { data } = await http.get<FocusSuggestion>(
      `/interviews/${id}/focus-suggestion`,
    )
    return data
  },

  /**
   * 开始面试：服务端会走完
   * `draft → preparing_context → planned → asking` 并生成首题。
   *
   * 对前端只是一个动作 —— 暴露三个转移会让这里必须懂状态机规则。
   */
  async start(id: number, query?: string, questionType = 'technical') {
    const { data } = await http.post<InterviewStartResult>(
      `/interviews/${id}/start`,
      { query: query ?? null, question_type: questionType },
    )
    return data
  },

  async questions(id: number) {
    const { data } = await http.get<InterviewQuestion[]>(
      `/interviews/${id}/questions`,
    )
    return data
  },

  /**
   * 取一道题的资料依据（片段正文）。
   *
   * 路径里带 `question_id` 而不是直接传 chunk id：后者会让
   * 任何登录用户都能读任意项目的片段（IDOR）。
   */
  async evidence(id: number, questionId: number) {
    const { data } = await http.get<InterviewEvidence>(
      `/interviews/${id}/questions/${questionId}/evidence`,
    )
    return data
  },

  /**
   * 提交作答并拿到本轮追问。
   *
   * `finished` 为 true 时 `follow_up_question` 为 null，
   * 且 `evaluation` 已带回结果 —— 说明题目预算耗尽、面试已自动结束。
   */
  async answer(id: number, questionId: number, answer: string) {
    const { data } = await http.post<InterviewAnswerResult>(
      `/interviews/${id}/answer`,
      { question_id: questionId, answer },
    )
    return data
  },

  async finish(id: number) {
    const { data } = await http.post<InterviewFinishResult>(
      `/interviews/${id}/finish`,
    )
    return data
  },

  async pause(id: number) {
    const { data } = await http.post<InterviewSession>(
      `/interviews/${id}/pause`,
    )
    return data
  },

  async resume(id: number) {
    const { data } = await http.post<InterviewSession>(
      `/interviews/${id}/resume`,
    )
    return data
  },

  async history(id: number) {
    const { data } = await http.get<StatusHistoryEntry[]>(
      `/interviews/${id}/history`,
    )
    return data
  },

  async evaluation(id: number) {
    const detail = await interviewApi.detail(id)
    return detail.evaluation as InterviewEvaluation | null
  },
}
