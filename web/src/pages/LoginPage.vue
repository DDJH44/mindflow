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
import { computed, onMounted, ref } from 'vue'
import { useRoute, useRouter } from 'vue-router'

import { authApi } from '../api'
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
const inviteCode = ref('')
const busy = ref(false)
const error = ref('')

/**
 * 本次部署是否要求邀请码。
 *
 * 默认 false，异步向服务端确认 —— 拿不到回答时**不显示**输入框：
 * 那样最坏情况是"该填但没显示"，后端会返回明确错误；
 * 而如果反过来（默认显示），本地开发会永远多一个没用的必填项。
 */
const requireInvite = ref(false)

onMounted(async () => {
  try {
    const modeInfo = await authApi.registrationMode()
    requireInvite.value = modeInfo.require_invite
  } catch {
    // 拿不到就按"不需要"处理 —— 注册时后端会给出准确原因。
  }
})

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
      await auth.register(
        account.value,
        email.value,
        password.value,
        // 只在需要时传；否则后端会把一个无关的码当成有效码去校验
        requireInvite.value ? inviteCode.value : undefined,
      )
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

        <!--
          邀请码：只在部署方开启注册控制时显示。

          文案说明"为什么需要它" —— 否则用户会以为这是
          注册流程里莫名其妙的一道坎，而不是"这个站限制了人数"。
        -->
        <div v-if="isRegister && requireInvite" class="field">
          <label for="invite-code">邀请码</label>
          <input
            id="invite-code"
            v-model.trim="inviteCode"
            autocomplete="off"
            spellcheck="false"
            required
            placeholder="例如 ABCD-2345-EFGH-6789"
          />
          <p class="faint" style="margin: 6px 0 0">
            这个站点限制注册人数，需要邀请码才能创建账号。
            每条码只能用一次（大小写和连字符都不影响）。
          </p>
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
