import vue from '@vitejs/plugin-vue'
import { defineConfig } from 'vite'

/**
 * 开发服务器把 `/api` 代理到后端。
 *
 * 为什么用代理而不是让前端直连 `http://localhost:8000`：
 * 直连会引入跨域预检，后端就得加 CORS 中间件 ——
 * 而生产环境通常是同域反向代理，为开发环境改生产配置不值得。
 *
 * 地址用环境变量覆盖，不硬编码：后端换端口时前端不用改代码。
 */
const BACKEND =
  process.env.VITE_BACKEND_ORIGIN ?? 'http://127.0.0.1:8000'

export default defineConfig({
  plugins: [vue()],
  server: {
    port: 5173,
    proxy: {
      '/api': {
        target: BACKEND,
        changeOrigin: true,
        // 生成题目与整场评价都会调用模型，可能数十秒
        timeout: 180_000,
      },
    },
  },
})
