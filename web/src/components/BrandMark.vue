<script setup lang="ts">
/**
 * 品牌字标。
 *
 * ## 为什么需要它
 *
 * 修订前全站只有纯文字 `MindFlow`。而实测的观感问题是
 * **"没有识别度"** —— 纯文字在深色顶栏上只是一个粗细正常的词，
 * 看起来像任何一个后台系统。
 *
 * ## 这个图形是什么意思
 *
 * 三根递增的竖条 + 最末一根用强调色：
 *
 * - **递增**：追问逐步深入（这是产品的核心动作）
 * - **三根**：对应"资料 → 问题 → 依据"三层结构
 * - **末根橙色**：表示"进行中/当前" —— 与全站强调色语义一致
 *
 * 它不是装饰：形状本身在说产品做什么。
 * 若换成圆形、菱形、抽象曲线，就变成"为了有个图标而有的图标"。
 *
 * ## 为什么内联 SVG 而不是图标库
 *
 * 这只是一个 3 个 `<rect>` 的图形。为它引入一个图标库，
 * 或引入一份图片资源，收益为负 ——
 * 而且图片在深色/浅色底上要准备两份。
 *
 * SVG 用 `currentColor` 之外的显式颜色：
 * 因为它出现在深色顶栏上，而那两处对比关系是固定的。
 */
</script>

<template>
  <span class="brandmark" aria-hidden="true">
    <svg viewBox="0 0 20 20" width="18" height="18">
      <!-- 三根递增高度的竖条，圆角 1px 与全站小圆角一致 -->
      <rect x="1" y="12" width="4" height="7" rx="1" fill="currentColor" />
      <rect x="8" y="7" width="4" height="12" rx="1" fill="currentColor" />
      <rect x="15" y="2" width="4" height="17" rx="1" class="brandmark-tip" />
    </svg>
  </span>
</template>
