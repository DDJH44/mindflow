/**
 * axios 实例：统一 baseURL、Token 注入、错误归一。
 *
 * 设计要点：
 * 1. **Token 只存一处**（localStorage 的单一 key），避免多处读写不一致。
 * 2. **错误归一**：后端把可预期的失败都映射成了明确的 HTTP 状态码
 *    （401 未认证 / 404 不存在 / 409 非法转移 / 422 校验 / 429 额度用尽），
 *    因此这里把它们抽成 `ApiError.status`，页面只需判断状态码，
 *    不必解析后端文案。
 * 3. **429 单独提示**：额度用尽与"无权访问"是完全不同的两件事，
 *    混在一起会让用户以为自己做错了什么。
 */

import axios, { AxiosError } from 'axios'

export const TOKEN_STORAGE_KEY = 'mindflow.token'

export interface ApiErrorBody {
  detail?: unknown
}

export class ApiError extends Error {
  status: number

  /** 后端返回的原始 detail，可能是字符串或校验错误数组。 */
  detail: unknown

  constructor(status: number, detail: unknown) {
    super(ApiError.describe(status, detail))
    this.name = 'ApiError'
    this.status = status
    this.detail = detail
  }

  /** 把后端 detail 转成可展示的一行文案。 */
  static describe(status: number, detail: unknown): string {
    if (typeof detail === 'string' && detail.trim()) {
      return detail
    }

    // pydantic 的 422 是数组，逐条拼出字段与原因
    if (Array.isArray(detail)) {
      const parts = detail.map((item) => {
        if (item && typeof item === 'object') {
          const loc = (item as { loc?: unknown[] }).loc ?? []
          const msg = (item as { msg?: string }).msg ?? ''
          const field = loc.filter((p) => p !== 'body').join('.')
          return field ? `${field}: ${msg}` : msg
        }
        return String(item)
      })
      if (parts.length) {
        return parts.join('；')
      }
    }

    const fallback: Record<number, string> = {
      400: '请求有误',
      401: '登录已过期，请重新登录',
      403: '没有权限',
      404: '资源不存在',
      409: '当前状态不允许这个操作',
      422: '提交的内容不符合要求',
      429: '本月额度已用完',
      500: '服务器出错了',
    }

    return fallback[status] ?? `请求失败（HTTP ${status}）`
  }

  /** 是否为"额度用尽"。页面据此给出与"无权访问"不同的提示。 */
  get isQuotaExceeded(): boolean {
    return this.status === 429
  }

  /** 是否为"未认证"，需要跳登录。 */
  get isUnauthorized(): boolean {
    return this.status === 401
  }
}

export function getToken(): string | null {
  return localStorage.getItem(TOKEN_STORAGE_KEY)
}

export function setToken(token: string): void {
  localStorage.setItem(TOKEN_STORAGE_KEY, token)
}

export function clearToken(): void {
  localStorage.removeItem(TOKEN_STORAGE_KEY)
}

export const http = axios.create({
  baseURL: import.meta.env.VITE_API_BASE_URL ?? '/api',
  timeout: 180_000, // 生成题目/整场评价会调用 LLM，可能数十秒
})

// 请求：注入 Token
http.interceptors.request.use((config) => {
  const token = getToken()
  if (token) {
    config.headers = config.headers ?? {}
    config.headers.Authorization = `Bearer ${token}`
  }
  return config
})

// 响应：把 axios 错误统一转成 ApiError
http.interceptors.response.use(
  (response) => response,
  (error: AxiosError<ApiErrorBody>) => {
    if (error.response) {
      const status = error.response.status
      const detail = error.response.data?.detail

      // 401 时顺手清掉失效 Token，避免后续请求继续带着它
      if (status === 401) {
        clearToken()
      }

      return Promise.reject(new ApiError(status, detail))
    }

    // 网络层失败（后端没起、断网、超时）
    const isTimeout = error.code === 'ECONNABORTED'
    return Promise.reject(
      new ApiError(
        0,
        isTimeout
          ? '请求超时。生成题目与评价需要调用模型，可能耗时较久，请稍后重试。'
          : '无法连接后端服务，请确认后端已启动。',
      ),
    )
  },
)
