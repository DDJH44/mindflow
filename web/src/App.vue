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
        MindFlow
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

    <main class="content">
      <RouterView />
    </main>
  </div>
</template>
