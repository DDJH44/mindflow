<script setup lang="ts">
/**
 * 面试历史。
 *
 * 存在的理由：没有这一页时用户关掉页面就**找不回进行中的面试** ——
 * 那场面试既占用了额度也无法继续，等于白做。
 *
 * 因此默认视图是「可继续的」（`unfinished_only`），
 * 而不是把所有历史平铺出来。用户打开这一页的目的通常只有一个：
 * "我之前那场没做完的面试在哪"。
 */
import { computed, onMounted, ref } from 'vue'
import { useRouter } from 'vue-router'

import { interviewApi } from '../api'
import { ApiError } from '../api/http'
import type { InterviewSessionListItem } from '../api/types'

const router = useRouter()

const items = ref<InterviewSessionListItem[]>([])
const total = ref(0)
const loading = ref(true)
const error = ref('')

/** 默认只看可继续的 —— 见文件头说明。 */
const onlyUnfinished = ref(true)
const openingId = ref<number | null>(null)

const PAGE_SIZE = 20

/**
 * 这些状态下用户可以直接回去继续。
 *
 * ⚠️ 必须包含 `created` / `draft`：库里存在旧值 `created`
 * （`LEGACY_STATUS_MAP` 会归一成 draft），而**实际已答过题的会话
 * 也可能停在草稿态**（实测有答了 3 题仍标 created 的）。
 * 少列这两个会让这类会话在列表里显示"—"、看起来打不开，
 * 而它们恰恰是用户最想找回来的。
 */
const RESUMABLE = new Set([
  'created',
  'draft',
  'preparing_context',
  'planned',
  'asking',
  'waiting_for_answer',
  'evaluating',
  'paused',
])

/**
 * 状态文案。
 *
 * 对"已作答但仍处于草稿态"的旧数据给出更准确的描述 ——
 * 一场答了 3 题的面试不该被叫作"草稿"。
 */
const STATUS_LABEL: Record<string, string> = {
  created: '未完成',
  draft: '未开始',
  preparing_context: '准备中',
  planned: '已计划',
  asking: '待作答',
  waiting_for_answer: '待作答',
  evaluating: '评价中',
  summarizing: '生成总结',
  completed: '已完成',
  paused: '已暂停',
  cancelled: '已取消',
  failed: '失败',
}

function statusLabel(status: string): string {
  return STATUS_LABEL[status] ?? status
}

function statusClass(status: string): string {
  if (status === 'completed') {
    return 'tag tag-ok'
  }
  if (status === 'cancelled' || status === 'failed') {
    return 'tag tag-warn'
  }
  if (status === 'paused') {
    return 'tag tag-warn'
  }
  if (RESUMABLE.has(status)) {
    return 'tag tag-primary'
  }
  return 'tag'
}

/** 能不能回去继续。 */
function isResumable(status: string): boolean {
  return RESUMABLE.has(status)
}

/** 已完成才有报告可看。 */
function hasReport(status: string): boolean {
  return status === 'completed'
}

const unfinishedCount = computed(
  () => items.value.filter((item) => isResumable(item.status)).length,
)

/**
 * 汇总文案。
 *
 * 默认视图已经筛掉了已完成的，因此不能只写"共 N 场"——
 * 用户会以为那是全部历史。把口径写在前面。
 */
const summaryText = computed(() => {
  if (onlyUnfinished.value) {
    return `只看未完成 · 共 ${total.value} 场（可继续 ${unfinishedCount.value} 场）`
  }
  return `全部历史 · 共 ${total.value} 场`
})

function formatTime(value: string): string {
  const date = new Date(value)
  if (Number.isNaN(date.getTime())) {
    return value
  }

  const diffMs = Date.now() - date.getTime()
  const minutes = Math.floor(diffMs / 60_000)

  if (minutes < 1) {
    return '刚刚'
  }
  if (minutes < 60) {
    return `${minutes} 分钟前`
  }

  const hours = Math.floor(minutes / 60)
  if (hours < 24) {
    return `${hours} 小时前`
  }

  const days = Math.floor(hours / 24)
  if (days < 30) {
    return `${days} 天前`
  }

  return date.toLocaleDateString()
}

async function load() {
  loading.value = true
  error.value = ''

  try {
    const result = await interviewApi.list({
      unfinished_only: onlyUnfinished.value,
      limit: PAGE_SIZE,
      offset: 0,
    })
    items.value = result.items
    total.value = result.total
  } catch (err) {
    error.value =
      err instanceof ApiError ? err.message : '加载失败'
  } finally {
    loading.value = false
  }
}

async function toggleFilter() {
  onlyUnfinished.value = !onlyUnfinished.value
  await load()
}

/**
 * 打开一场面试。
 *
 * 未完成的进面试页继续（暂停中的会话在那边有"继续面试"按钮）；
 * 已完成的直接去报告 —— 让用户少点一次。
 */
async function open(item: InterviewSessionListItem) {
  if (openingId.value !== null) {
    return
  }

  openingId.value = item.id

  try {
    if (hasReport(item.status)) {
      await router.push({
        name: 'report',
        params: { id: item.id },
      })
    } else {
      await router.push({
        name: 'interview',
        params: { id: item.id },
      })
    }
  } finally {
    openingId.value = null
  }
}

onMounted(load)
</script>

<template>
  <div>
    <div class="page-head">
      <div class="row-between">
        <div>
          <h1>面试历史</h1>
          <p class="muted" style="margin: 0">
            继续没做完的面试，或回看以前的报告。
          </p>
        </div>
        <RouterLink class="link" :to="{ name: 'home' }">
          开新面试
        </RouterLink>
      </div>
    </div>

    <div v-if="error" class="alert alert-error">{{ error }}</div>

    <!-- 筛选 -->
    <div class="card">
      <div class="row-between">
        <div class="row">
          <button
            class="btn-ghost"
            type="button"
            :disabled="loading"
            @click="toggleFilter"
          >
            {{ onlyUnfinished ? '只看未完成' : '查看全部' }}
          </button>
          <span class="muted">{{ summaryText }}</span>
        </div>
        <button
          class="link"
          type="button"
          :disabled="loading"
          @click="load"
        >
          刷新
        </button>
      </div>
    </div>

    <div v-if="loading" class="card">
      <span class="spinner" />正在加载…
    </div>

    <!-- 列表 -->
    <div v-else-if="!items.length" class="card">
      <p class="muted" style="margin: 0">
        <template v-if="onlyUnfinished">
          没有未完成的面试。
        </template>
        <template v-else>
          还没有任何面试记录。
        </template>
      </p>
      <div class="row" style="margin-top: 12px">
        <RouterLink class="link" :to="{ name: 'home' }">
          去开一场面试
        </RouterLink>
      </div>
    </div>

    <div v-else class="list">
      <div
        v-for="item in items"
        :key="item.id"
        class="list-item"
        @click="open(item)"
      >
        <div style="min-width: 0; flex: 1">
          <div
            style="
              display: flex;
              align-items: center;
              gap: 8px;
              flex-wrap: wrap;
            "
          >
            <span>{{ item.target_role || '模拟面试' }}</span>
            <span :class="statusClass(item.status)">
              {{ statusLabel(item.status) }}
            </span>
            <span v-if="item.project_name" class="tag">
              {{ item.project_name }}
            </span>
          </div>

          <div class="faint" style="margin-top: 2px">
            已作答 {{ item.answered_count }} /
            {{ item.max_questions }} 题 · {{ formatTime(item.updated_at) }}
            <template v-if="item.termination_reason === 'budget_exhausted'">
              · 达到题目上限后自动结束
            </template>
          </div>
        </div>

        <span style="flex-shrink: 0">
          <span v-if="openingId === item.id" class="spinner" />
          <span v-else-if="hasReport(item.status)" class="link">
            看报告
          </span>
          <span
            v-else-if="isResumable(item.status)"
            class="link"
          >
            {{ item.status === 'paused' ? '继续面试' : '回到面试' }}
          </span>
          <span v-else class="faint">—</span>
        </span>
      </div>
    </div>

    <p
      v-if="items.length && total > items.length"
      class="faint"
      style="margin-top: 10px"
    >
      只显示最近 {{ items.length }} 场（共 {{ total }} 场）。
    </p>
  </div>
</template>
