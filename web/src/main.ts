import { createPinia } from 'pinia'
import { createApp } from 'vue'

import App from './App.vue'
import { router } from './router'
// 应用样式。脚手架的 `style.css` 已于 2026-10-12 删除 ——
// 它从未被 import，留着只会让人误以为它生效。
import './styles/app.css'

createApp(App).use(createPinia()).use(router).mount('#app')
