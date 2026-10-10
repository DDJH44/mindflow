<script setup lang="ts">
/**
 * 极简趋势图（内联 SVG）。
 *
 * 为什么不引图表库：这里只需要 4 条不到 10 个点的折线，
 * 而一个图表库会带来主题、响应式、tree-shaking 等一整套问题。
 *
 * 为什么不用平滑曲线：折线让人一眼看出"这是一次次的测量"，
 * 而平滑曲线会暗示中间存在连续变化 —— 面试分数没有中间态。
 */
import { computed } from 'vue'

const props = defineProps<{
  /** 逐次分数，**按时间正序**（旧 → 新）。 */
  values: number[]
  label: string
}>()

const WIDTH = 160
const HEIGHT = 40
const PADDING = 4

/**
 * 纵轴范围。
 *
 * 用**数据自身的** min/max，不用固定 0–100：
 * 面试分数集中在 30–90，固定量程会把趋势压成一条直线，
 * 反而看不出变化。
 *
 * 但两点必须处理：
 * - 只有 1 个点时 min == max，除零
 * - 变化极小时放大过度，会让 2 分的差异看起来像悬崖
 */
const range = computed(() => {
  const values = props.values

  if (!values.length) {
    return { min: 0, max: 100 }
  }

  const min = Math.min(...values)
  const max = Math.max(...values)

  if (min === max) {
    // 单点或全等：给一个对称区间，线画在中间
    return { min: Math.max(0, min - 10), max: min + 10 }
  }

  // 留一点上下余量，避免折线贴边
  const margin = Math.max(2, (max - min) * 0.15)
  return { min: min - margin, max: max + margin }
})

const points = computed(() => {
  const values = props.values

  if (values.length < 2) {
    return []
  }

  const { min, max } = range.value
  const span = max - min || 1

  const stepX =
    (WIDTH - PADDING * 2) / (values.length - 1)

  return values.map((value, index) => ({
    x: PADDING + index * stepX,
    y:
      HEIGHT -
      PADDING -
      ((value - min) / span) * (HEIGHT - PADDING * 2),
  }))
})

const polyline = computed(() =>
  points.value.map((point) => `${point.x},${point.y}`).join(' '),
)

/**
 * 单点也要画出来。
 *
 * 只有一场面试时没有折线，但**必须显示一个点** ——
 * 否则用户看到的是一片空白，会以为功能坏了。
 */
const singlePoint = computed(() => {
  if (props.values.length !== 1) {
    return null
  }

  return { x: WIDTH / 2, y: HEIGHT / 2 }
})
</script>

<template>
  <svg
    :width="WIDTH"
    :height="HEIGHT"
    :viewBox="`0 0 ${WIDTH} ${HEIGHT}`"
    class="sparkline"
    role="img"
    :aria-label="`${label}的逐次分数趋势`"
  >
    <polyline
      v-if="polyline"
      :points="polyline"
      fill="none"
      stroke="currentColor"
      stroke-width="1.5"
      stroke-linejoin="round"
      stroke-linecap="round"
    />

    <circle
      v-for="(point, index) in points"
      :key="index"
      :cx="point.x"
      :cy="point.y"
      r="2"
      fill="currentColor"
    />

    <circle
      v-if="singlePoint"
      :cx="singlePoint.x"
      :cy="singlePoint.y"
      r="2.5"
      fill="currentColor"
    />
  </svg>
</template>
