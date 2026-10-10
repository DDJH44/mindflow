import { createRouter, createWebHistory } from 'vue-router'

import { useAuthStore } from './stores/auth'

/**
 * 路由与守卫。
 *
 * 守卫在这里做两件事：
 * 1. **先恢复登录态再判断**（`restore()` 内部只拉一次），
 *    否则刷新页面时 `user` 还是 null，会被误判成未登录。
 * 2. 未登录访问受保护路由时带上 `redirect`，登录后回到原目标 ——
 *    否则用户从深链接进来会被丢回首页。
 */
export const router = createRouter({
  history: createWebHistory(),
  routes: [
    {
      path: '/login',
      name: 'login',
      component: () => import('./pages/LoginPage.vue'),
      meta: { public: true, title: '登录' },
    },
    {
      path: '/',
      name: 'home',
      component: () => import('./pages/HomePage.vue'),
      meta: { title: '面试' },
    },
    {
      path: '/history',
      name: 'history',
      component: () => import('./pages/HistoryPage.vue'),
      meta: { title: '面试历史' },
    },
    {
      path: '/projects/:id',
      name: 'project',
      component: () => import('./pages/ProjectPage.vue'),
      props: (route) => ({ id: Number(route.params.id) }),
      meta: { title: '项目资料' },
    },
    {
      path: '/interviews/:id',
      name: 'interview',
      component: () => import('./pages/InterviewPage.vue'),
      props: (route) => ({ id: Number(route.params.id) }),
      meta: { title: '面试进行中' },
    },
    {
      path: '/interviews/:id/report',
      name: 'report',
      component: () => import('./pages/ReportPage.vue'),
      props: (route) => ({ id: Number(route.params.id) }),
      meta: { title: '面试报告' },
    },
    {
      path: '/:pathMatch(.*)*',
      redirect: '/',
    },
  ],
})

router.beforeEach(async (to) => {
  const auth = useAuthStore()
  await auth.restore()

  if (!to.meta.public && !auth.user) {
    return { name: 'login', query: { redirect: to.fullPath } }
  }

  // 已登录时不必再看到登录页
  if (to.name === 'login' && auth.user) {
    return { name: 'home' }
  }

  return true
})

router.afterEach((to) => {
  const title = to.meta.title
  document.title = title ? `${title} · MindFlow` : 'MindFlow'
})
