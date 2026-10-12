<script setup lang="ts">
/**
 * 页面级状态区：错误提示 + 加载占位。
 *
 * ## 为什么抽这一个，而不是抽"卡片"
 *
 * 抽组件必须先证明**有重复可消除**，否则只是把代码搬了个位置
 * 并多一层 props 传递。实测 7 个页面的模板（1163 行）后：
 *
 *     <div v-if="error" class="alert alert-error">{{ error }}</div>
 *     <div v-if="loading" class="card">
 *       <span class="spinner" />正在加载…
 *     </div>
 *
 * 这两段**逐字重复**出现在 6 个页面里（`measure_dup` 统计）。
 * 而 `.card` 本身是 **CSS 类**，已经承担了"统一外观"的职责 ——
 * 各页面的卡片**结构**并不相同，抽它需要大量插槽，
 * 换来的只是"读一处变成跳两个文件"。
 *
 * 判据是**逐字相同**，不是"看起来像"。
 *
 * ## 为什么它不接收 `loading` 属性
 *
 * 第一版让组件自己判断 `v-if="loading"`。那看起来更省事，
 * 却**弄坏了各页面的 `v-if/v-else-if` 链**：
 *
 *     <PageState :loading="loading" />   ← v-if 在这里
 *     <template v-else-if="session">      ← 链断了
 *
 * 而 Vue 靠这条链把 `session` 从 `T | null` 收窄成 `T`。
 * 链一断，所有 `session.xxx` 都报
 * `TS18047: 'session' is possibly 'null'` —— 实测 6 个页面
 * 一共十几处。
 *
 * 因此改为**纯展示组件**：条件由父级的分支控制，
 * 它只负责"把给定的状态画出来"。副作用是接口也更简单
 * （不必把 `loading` 同时传给分支和组件）。
 */
withDefaults(
  defineProps<{
    /** 错误文案。为空则不显示错误区。 */
    error?: string
    /**
     * 加载文案。
     *
     * 默认"正在加载…"覆盖 4 个页面；另外两个需要更具体的
     * 说法（"正在加载面试…" / "正在加载报告…"）——
     * **具体文案对用户更有用**，因此不做成统一那一句。
     */
    loadingText?: string
  }>(),
  {
    error: '',
    loadingText: '正在加载…',
  },
)
</script>

<template>
  <!--
    顺序固定为"错误在前"：用户最需要先看到的是失败原因，
    而不是一个还在转的加载框。
  -->
  <div v-if="error" class="alert alert-error">{{ error }}</div>

  <!--
    默认插槽：给"错误与加载之间还有别的内容"的页面用。
    目前只有面试历史页需要 —— 它的筛选卡片在加载时仍要可见
    （按钮处于禁用态），因此不能把加载占位放到最前面。
  -->
  <slot />

  <div class="card">
    <span class="spinner" />{{ loadingText }}
  </div>
</template>
