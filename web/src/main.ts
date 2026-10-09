import { createPinia } from 'pinia'
import { createApp } from 'vue'

import App from './App.vue'
import { router } from './router'
// 应用样式与脚手架的 style.css 分开：后者属于 Vite 模板，
// 保留可随时对照/回退，不删。
import './styles/app.css'

createApp(App).use(createPinia()).use(router).mount('#app')
