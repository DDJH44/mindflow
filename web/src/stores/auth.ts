/**
 * 认证状态。
 *
 * Token 的存取统一走 `api/http`（单一 key），这里只维护"当前用户"。
 *
 * 为什么不在启动时无条件拉一次 `/auth/me`：
 * 那会让未登录的访客也吃一次 401，并且首屏要等一个必然失败的请求。
 * 因此先看本地有没有 Token，有才去验证 —— 顺带起到"Token 是否还有效"的检查作用。
 */

import { defineStore } from 'pinia'
import { ref } from 'vue'

import { authApi } from '../api'
import {
  ApiError,
  clearToken,
  getToken,
  setToken,
} from '../api/http'
import type { User } from '../api/types'

export const useAuthStore = defineStore('auth', () => {
  const user = ref<User | null>(null)
  const ready = ref(false)

  async function login(account: string, password: string) {
    const token = await authApi.login(account, password)
    setToken(token.access_token)
    user.value = await authApi.me()
  }

  async function register(
    username: string,
    email: string,
    password: string,
  ) {
    await authApi.register(username, email, password)
    await login(username, password)
  }

  /**
   * 恢复登录态。
   *
   * 必须在路由放行**之前**完成，否则守卫会误判未登录。
   * Token 失效（401）时清掉本地状态，让守卫把用户送去登录页。
   */
  async function restore() {
    if (ready.value) {
      return
    }

    if (!getToken()) {
      ready.value = true
      return
    }

    try {
      user.value = await authApi.me()
    } catch (error) {
      if (error instanceof ApiError && error.isUnauthorized) {
        clearToken()
      }
      user.value = null
    } finally {
      ready.value = true
    }
  }

  function logout() {
    clearToken()
    user.value = null
  }

  return { user, ready, login, register, restore, logout }
})
