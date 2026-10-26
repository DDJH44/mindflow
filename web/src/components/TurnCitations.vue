<script setup lang="ts">
/**
 * 结论的出处处标：把"这条结论依据第几轮回答"渲染成可点的小标签。
 *
 * ## 为什么做成独立组件

 * 三组列表（优势/不足/建议）都要用同一套渲染与交互。写在页面里
 * 会重复三遍，而重复的地方最容易出现"改了一处漏了另两处"。
 *
 * ## 为什么 `turns` 为空就整块不渲染

 * 没有出处时**不能**显示"未标注出处"这类占位 —— 报告里每条结论
 * 都跟着一句话会把版面淹掉，而用户真正需要的是"能点的时候能点"。
 * 出处缺失本身是模型没标，不是用户要处理的问题。
 */
defineProps<{ turns: number[] }>()

const emit = defineEmits<{ (event: 'jump', turn: number): void }>()
</script>

<template>
  <span v-if="turns.length" class="turn-citations">
    <span class="faint">依据</span>
    <button
      v-for="turn in turns"
      :key="turn"
      class="turn-chip"
      type="button"
      :title="`跳到第 ${turn} 题`"
      @click="emit('jump', turn)"
    >
      第 {{ turn }} 题
    </button>
  </span>
</template>
