/**
 * 各域接口封装。
 *
 * 一个域一个文件，只做"路径 + 方法 + 类型"的映射，
 * 不放业务判断 —— 状态机是否允许某操作由后端决定（§10），
 * 前端只负责展示 `allowed_transitions`。
 */

import { http } from './http'
import type {
  DocumentItem,
  DocumentType,
  InterviewAnswerResult,
  InterviewDetail,
  InterviewEvaluation,
  InterviewFinishResult,
  InterviewQuestion,
  InterviewSession,
  InterviewStartResult,
  Project,
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

  async register(username: string, email: string, password: string) {
    const { data } = await http.post<{ message: string; user: User }>(
      '/auth/register',
      { username, email, password },
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
  async current() {
    const { data } = await http.get<Usage>('/usage')
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
