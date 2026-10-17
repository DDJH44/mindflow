<script setup lang="ts">
/**
 * 应用外壳。
 *
 * 只在已登录时显示顶栏 —— 登录页自己有完整布局，
 * 顶栏露出"退出登录"会让人以为已经登录了。
 */
import { computed } from 'vue'
import { useRouter } from 'vue-router'

import { useAuthStore } from './stores/auth'
import BrandMark from './components/BrandMark.vue'

const auth = useAuthStore()
const router = useRouter()

const showChrome = computed(() => Boolean(auth.user))

function logout() {
  auth.logout()
  router.push({ name: 'login' })
}
</script>

<template>
  <div class="app">
    <header v-if="showChrome" class="topbar">
      <RouterLink class="brand" :to="{ name: 'home' }">
        <BrandMark />
        <span>MindFlow</span>
      </RouterLink>

      <!--
        导航放在品牌旁边而不是右侧操作区：
        右侧是"账号相关"（用户名、退出），把页面入口混进去
        会让用户以为"面试历史"是账号功能。
      -->
      <nav class="topnav">
        <RouterLink :to="{ name: 'home' }">开面试</RouterLink>
        <RouterLink :to="{ name: 'history' }">历史</RouterLink>
        <RouterLink :to="{ name: 'profile' }">能力画像</RouterLink>
      </nav>

      <div class="spacer" />

      <span class="who">{{ auth.user?.username }}</span>
      <button class="link" type="button" @click="logout">
        退出登录
      </button>
    </header>

    <!--
      登录页**不套 `.content`**。

      `.content` 带 `max-width` 与左右 padding，而登录页是
      **整页深色**布局 —— 套进去就会出现白边与底部灰条。
      实测踩过：改成深色底之后，登录页四周出现一圈白边，
      看起来像渲染坏了。

      因此登录页自己负责整页布局（`page-full`），
      其它页面才用 `.content` 约束阅读宽度。
    -->
    <main v-if="showChrome" class="content">
      <RouterView />
    </main>

    <main v-else class="page-full">
      <RouterView />
    </main>
  </div>
</template>
