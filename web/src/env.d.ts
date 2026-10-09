/// <reference types="vite/client" />

/**
 * Vue SFC 与 Vite 环境变量的类型声明。
 *
 * 脚手架没有生成这个文件，因此 `import X from './X.vue'`
 * 与 `import.meta.env.XXX` 都会报 TS2307 / TS2339。
 */

declare module '*.vue' {
  import type { DefineComponent } from 'vue'

  const component: DefineComponent<
    Record<string, unknown>,
    Record<string, unknown>,
    unknown
  >
  export default component
}

interface ImportMetaEnv {
  /** 后端地址覆盖；不设时走 Vite 代理的 `/api`。 */
  readonly VITE_API_BASE_URL?: string
}

interface ImportMeta {
  readonly env: ImportMetaEnv
}
