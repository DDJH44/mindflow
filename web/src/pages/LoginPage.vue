<script setup lang="ts">
/**
 * 登录 / 注册。
 *
 * 两种模式共用一个表单：注册成功后自动登录，
 * 省掉"注册完再回去登录"的一步。
 *
 * 登录后用 `redirect` 回到用户原本要去的页面 ——
 * 直接从深链接进来时不该被丢回首页。
 */
import { computed, ref } from 'vue'
import { useRoute, useRouter } from 'vue-router'

import { ApiError } from '../api/http'
import BrandMark from '../components/BrandMark.vue'
import PageState from '../components/PageState.vue'
import { useAuthStore } from '../stores/auth'

const auth = useAuthStore()
const router = useRouter()
const route = useRoute()

const mode = ref<'login' | 'register'>('login')
const account = ref('')
const email = ref('')
const password = ref('')
const busy = ref(false)
const error = ref('')

const isRegister = computed(() => mode.value === 'register')
const submitLabel = computed(() =>
  isRegister.value ? '注册并登录' : '登录',
)

function switchMode() {
  mode.value = isRegister.value ? 'login' : 'register'
  error.value = ''
}

async function submit() {
  if (busy.value) {
    return
  }

  error.value = ''
  busy.value = true

  try {
    if (isRegister.value) {
      await auth.register(account.value, email.value, password.value)
    } else {
      await auth.login(account.value, password.value)
    }

    const redirect = route.query.redirect
    await router.replace(
      typeof redirect === 'string' ? redirect : { name: 'home' },
    )
  } catch (err) {
    error.value =
      err instanceof ApiError ? err.message : '操作失败，请重试'
  } finally {
    busy.value = false
  }
}
</script>

<template>
  <div class="center-page">
    <!--
      只用 `auth-card`，**不叠加 `card`**。

      两者都定义 padding 与圆角，叠加会让内边距忽大忽小
      （取决于哪条规则胜出），而 `auth-card` 已自带完整外观。
    -->
    <div class="auth-card">
      <div class="page-head">
        <h1 class="auth-brand">
          <BrandMark />
          <span>MindFlow</span>
        </h1>
        <p class="muted">
          上传资料，完成一轮有依据的模拟面试。
        </p>
      </div>

      <form class="stack" @submit.prevent="submit">
        <!--
          登录页**没有加载态**（表单提交时按钮置灰即可），
          因此关掉加载占位 —— 否则会一直显示"正在加载…"，
          看起来像卡死了。
        -->
        <PageState :error="error" :show-loading="false" />

        <div class="field">
          <label for="account">
            {{ isRegister ? '用户名' : '用户名或邮箱' }}
          </label>
          <input
            id="account"
            v-model.trim="account"
            autocomplete="username"
            :minlength="3"
            required
            :placeholder="isRegister ? '至少 3 个字符' : ''"
          />
        </div>

        <div v-if="isRegister" class="field">
          <label for="email">邮箱</label>
          <input
            id="email"
            v-model.trim="email"
            type="email"
            autocomplete="email"
            required
          />
        </div>

        <div class="field">
          <label for="password">密码</label>
          <input
            id="password"
            v-model="password"
            type="password"
            :autocomplete="
              isRegister ? 'new-password' : 'current-password'
            "
            minlength="6"
            required
            placeholder="至少 6 个字符"
          />
        </div>

        <button
          class="btn-primary btn-block"
          type="submit"
          :disabled="busy"
        >
          <span v-if="busy" class="spinner" />
          {{ busy ? '请稍候…' : submitLabel }}
        </button>
      </form>

      <!--
        `align-items: center` 是必须的：`.row` 默认 `align-items: center`
        但 `span` 与 `button` 的行高不同，会出现"文字对不齐"的观感。
        改用专门的 auth-switch 类，同时把行内 style 收进 CSS。
      -->
      <div class="auth-switch">
        <span class="muted">
          {{ isRegister ? '已有账号？' : '还没有账号？' }}
        </span>
        <button class="link" type="button" @click="switchMode">
          {{ isRegister ? '去登录' : '去注册' }}
        </button>
      </div>
    </div>
  </div>
</template>
