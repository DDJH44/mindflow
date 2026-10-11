<script setup lang="ts">
/**
 * 长等待的进度提示。
 *
 * ## 它解决什么
 *
 * 单轮答题要等模型分析回答 + 生成追问，实测 **4.9s ~ 60.8s**
 * （21 次请求，均值约 23s，最慢是均值的 2.6 倍）。
 * 而此前界面上只有一个转圈加一句静态的"通常十几秒" ——
 * 用户看不出**现在是正常等待还是卡死了**。
 *
 * ## 一个刻意的诚实取舍：不假装知道"进行到哪一步"
 *
 * 服务端确实分几个阶段（保存回答 → 分析回答 → 生成追问），
 * 但**浏览器拿不到"正在哪一步"**：那些状态转移用
 * `commit=False`、在请求末尾才一次提交，期间查库看不到。
 *
 * 因此这里**只显示确定成立的两样东西**：
 *
 * 1. **已等待秒数** —— 它是真的，而且它证明界面没卡死
 * 2. **这个请求要做的几件事** —— 按后端真实顺序列出，
 *    但**不声称哪一步已经完成**
 *
 * ⚠️ 我第一版把阶段做成"逐条打勾"来模拟进度，
 * 截图复核时发现它在 32 秒时就显示"正在准备下一个问题" ——
 * 而那时模型**多半还在分析**。那是**把估算当成了事实**，
 * 代价是让用户误以为系统在精确汇报。
 * 现在只用一条流动的进度条表示"整体进行中"，
 * 文案是"将要做什么"而不是"已经做到了什么"。
 */
import { computed, onUnmounted, ref, watch } from 'vue'

const props = withDefaults(
  defineProps<{
    /** 是否正在等待 */
    active: boolean
    /** 这个请求将要做的事（按后端真实顺序） */
    steps?: string[]
    /** 超过多少秒后追加一句安抚说明 */
    reassureAfter?: number
    /** 是否显示"通常多久"的说明 */
    showDurationHint?: boolean
  }>(),
  {
    steps: () => [
      '保存你的回答',
      '分析回答内容',
      '准备下一个问题或整场评价',
    ],
    // 取 30：实测最慢 60.8s，而均值 23s。
    // 30 秒之后仍未返回才值得安抚 —— 常驻会变成噪音。
    reassureAfter: 30,
    showDurationHint: true,
  },
)

const elapsed = ref(0)
let timer: number | null = null

function stop() {
  if (timer !== null) {
    window.clearInterval(timer)
    timer = null
  }
}

function start() {
  stop()
  elapsed.value = 0

  timer = window.setInterval(() => {
    elapsed.value += 1
  }, 1000)
}

watch(
  () => props.active,
  (active) => {
    if (active) {
      start()
    } else {
      stop()
    }
  },
  { immediate: true },
)

onUnmounted(stop)

const isLongWait = computed(
  () => elapsed.value >= props.reassureAfter,
)

/**
 * 秒数的展示。
 *
 * 60 秒以内给秒数（更精确、更有"在动"的感觉）；
 * 超过则给"约 N 分钟" —— 用户不需要精确到秒，
 * 而不断跳动的三位数秒数会让人感觉更慢。
 */
const elapsedLabel = computed(() => {
  if (elapsed.value < 60) {
    return `已等待 ${elapsed.value} 秒`
  }

  const minutes = Math.floor(elapsed.value / 60)
  const seconds = elapsed.value % 60
  return `已等待 ${minutes} 分 ${seconds} 秒`
})
</script>

<template>
  <div v-if="active" class="waiting" role="status" aria-live="polite">
    <div class="row-between">
      <div class="row">
        <span class="spinner" />
        <strong>模型正在处理</strong>
      </div>
      <span class="faint">{{ elapsedLabel }}</span>
    </div>

    <!--
      不确定进度条：来回流动而不是按百分比填充。
      百分比需要一个我们并不掌握的真实进度；
      流动只表示"仍在进行"，那是事实。
    -->
    <div class="waiting-track">
      <div class="waiting-track-fill" />
    </div>

    <p class="faint waiting-steps">
      这一步会依次：{{ steps.join(' → ') }}
    </p>

    <!--
      超时安抚：只在真的慢时才出现。
      常驻会变成噪音，而它要解决的恰恰是"偶发慢"造成的怀疑。
    -->
    <p v-if="isLongWait" class="waiting-reassure">
      模型响应比平时慢。请求仍在处理中，请再等一会儿 ——
      回答已经保存，不会丢失。
    </p>

    <p v-if="showDurationHint" class="faint" style="margin: 6px 0 0">
      实测通常十几秒到半分钟，最慢约一分钟。
    </p>
  </div>
</template>
