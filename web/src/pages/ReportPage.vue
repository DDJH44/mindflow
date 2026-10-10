<script setup lang="ts">
/**
 * 面试报告页。
 *
 * 展示后端已落库的评价：四项分数、优势 / 风险 / 建议、
 * 文字反馈，以及完整问答记录。
 *
 * 分数直接显示后端给的整数 —— `scoring_details` 里记录了
 * 锚点与采样口径（ADR-021），前端不重新解释分数含义。
 */
import { computed, onMounted, ref } from 'vue'

import { interviewApi } from '../api'
import { ApiError } from '../api/http'
import type { InterviewDetail } from '../api/types'
import EvidencePanel from '../components/EvidencePanel.vue'

const props = defineProps<{ id: number }>()

const detail = ref<InterviewDetail | null>(null)
const loading = ref(true)
const error = ref('')

/** 当前展开了资料依据的问题 id。 */
const openEvidenceId = ref<number | null>(null)

function toggleEvidence(questionId: number) {
  openEvidenceId.value =
    openEvidenceId.value === questionId ? null : questionId
}

const evaluation = computed(() => detail.value?.evaluation ?? null)
const session = computed(() => detail.value?.session ?? null)
const questions = computed(() => detail.value?.questions ?? [])

const scores = computed(() => {
  const value = evaluation.value
  if (!value) {
    return []
  }
  return [
    { label: '综合', value: value.overall_score },
    { label: '技术', value: value.technical_score },
    { label: '项目', value: value.project_score },
    { label: '沟通', value: value.communication_score },
  ]
})

/** 结束原因的展示文案。 */
const terminationHint = computed(() => {
  const reason = session.value?.termination_reason
  if (reason === 'budget_exhausted') {
    return '本次面试达到题目数量上限后自动结束。'
  }
  if (reason === 'user_finished') {
    return '本次面试由你主动结束。'
  }
  return ''
})

/** 评分口径摘要，让分数可事后解释。 */
const scoringHint = computed(() => {
  const details = evaluation.value?.scoring_details as
    | Record<string, unknown>
    | undefined
  const config = details?.scoring_config as
    | Record<string, unknown>
    | undefined
  if (!config) {
    return ''
  }
  const samples = config.evaluation_samples
  const model = config.model
  return `评分口径：${String(model ?? '默认模型')}，采样 ${String(samples ?? '?')} 次（离线口径多次采样，与线上单次不可直接比较）。`
})

async function load() {
  loading.value = true
  error.value = ''

  try {
    detail.value = await interviewApi.detail(props.id)
  } catch (err) {
    error.value =
      err instanceof ApiError ? err.message : '加载报告失败'
  } finally {
    loading.value = false
  }
}

onMounted(load)
</script>

<template>
  <div>
    <div v-if="error" class="alert alert-error">{{ error }}</div>

    <div v-if="loading" class="card">
      <span class="spinner" />正在加载报告…
    </div>

    <template v-else-if="session">
      <div class="page-head">
        <h1>面试报告</h1>
        <p class="muted" style="margin: 0">
          {{ session.target_role || '模拟面试' }} · 共
          {{ questions.length }} 题
        </p>
        <p v-if="terminationHint" class="faint" style="margin: 4px 0 0">
          {{ terminationHint }}
        </p>
      </div>

      <!-- 未完成 -->
      <div v-if="!evaluation" class="card">
        <p class="muted" style="margin: 0">
          这场面试还没有生成评价。回到面试页点"结束并生成报告"即可。
        </p>
        <div class="row" style="margin-top: 12px">
          <RouterLink
            class="link"
            :to="{ name: 'interview', params: { id } }"
          >
            回到面试
          </RouterLink>
        </div>
      </div>

      <template v-else>
        <!-- 分数 -->
        <div class="score-grid">
          <div v-for="item in scores" :key="item.label" class="score-cell">
            <div class="score-value">{{ item.value }}</div>
            <div class="score-label">{{ item.label }}</div>
          </div>
        </div>

        <p v-if="scoringHint" class="faint" style="margin-top: 8px">
          {{ scoringHint }}
        </p>

        <!-- 反馈 -->
        <div v-if="evaluation.feedback" class="card">
          <h2>整体反馈</h2>
          <p style="margin: 0">{{ evaluation.feedback }}</p>
        </div>

        <!-- 优势 -->
        <div v-if="evaluation.strengths.length" class="card">
          <h2>表现出的优势</h2>
          <ul class="bullet-list">
            <li v-for="(item, index) in evaluation.strengths" :key="index">
              {{ item }}
            </li>
          </ul>
        </div>

        <!-- 风险 -->
        <div v-if="evaluation.weaknesses.length" class="card">
          <h2>需要留意的不足</h2>
          <ul class="bullet-list">
            <li v-for="(item, index) in evaluation.weaknesses" :key="index">
              {{ item }}
            </li>
          </ul>
        </div>

        <!-- 建议 -->
        <div v-if="evaluation.suggestions.length" class="card">
          <h2>改进建议</h2>
          <ul class="bullet-list">
            <li v-for="(item, index) in evaluation.suggestions" :key="index">
              {{ item }}
            </li>
          </ul>
        </div>
      </template>

      <!-- 问答记录 -->
      <div class="card">
        <h2>完整问答记录</h2>

        <div v-if="!questions.length" class="empty">
          没有记录到问答。
        </div>

        <div
          v-for="item in questions"
          :key="item.id"
          class="history-item"
        >
          <div style="margin-bottom: 4px">
            <span class="faint">第 {{ item.question_index + 1 }} 题</span>
            <span
              v-if="item.question_type === 'follow_up'"
              class="tag tag-primary"
              style="margin-left: 6px"
            >
              追问
            </span>
            <button
              v-if="!item.is_general && item.evidence_chunk_ids.length"
              class="evidence-toggle"
              type="button"
              style="margin-left: 8px"
              @click="toggleEvidence(item.id)"
            >
              资料依据 {{ item.evidence_chunk_ids.length }} 段
              {{ openEvidenceId === item.id ? '▲' : '▼' }}
            </button>
            <span
              v-else-if="item.is_general"
              class="tag"
              style="margin-left: 6px"
            >
              通用能力题
            </span>
          </div>

          <div style="font-weight: 500">{{ item.question }}</div>

          <!-- 报告里也给出依据：这是"答案有据可查"的最后落点 -->
          <EvidencePanel
            v-if="openEvidenceId === item.id"
            :session-id="id"
            :question-id="item.id"
          />

          <div v-if="item.answer" class="muted" style="margin-top: 6px">
            {{ item.answer }}
          </div>
          <div v-else class="faint" style="margin-top: 6px">未作答</div>
        </div>
      </div>

      <div class="row" style="margin-top: 20px">
        <RouterLink class="link" :to="{ name: 'home' }">
          再开一场面试
        </RouterLink>
      </div>
    </template>

    <div v-else class="card">
      <p class="muted">面试不存在或已被删除。</p>
      <RouterLink class="link" :to="{ name: 'home' }">
        返回首页
      </RouterLink>
    </div>
  </div>
</template>
