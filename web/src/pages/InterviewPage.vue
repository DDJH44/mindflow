<script setup lang="ts">
/**
 * 面试进行页。
 *
 * 核心是维护"当前待回答的问题"：
 * 从 `/detail` 取到的题目列表里，最后一道未作答的就是待答题。
 * 不自己在本地推进索引 —— 一旦刷新或从报告页返回，
 * 本地索引就与服务端不一致，而以服务端的问答状态为准永远是对的。
 *
 * 另外两件事：
 * - 后端在题目预算耗尽时会**自动结束**面试并带回评价
 *   （`finished=true`），此时直接跳报告页，不要求用户再点"结束"。
 * - 追问可能是 `null`（预算刚好用尽），也要走上面的分支。
 */
import { computed, onMounted, ref } from 'vue'
import { useRouter } from 'vue-router'

import { interviewApi } from '../api'
import { ApiError } from '../api/http'
import type {
  InterviewDetail,
  InterviewEvaluation,
  InterviewQuestion,
} from '../api/types'

const props = defineProps<{ id: number }>()

const router = useRouter()

const detail = ref<InterviewDetail | null>(null)
const answerText = ref('')
const loading = ref(true)
const submitting = ref(false)
const finishing = ref(false)
const error = ref('')

/** 服务端带回的自动评价（预算耗尽时）。 */
const autoEvaluation = ref<InterviewEvaluation | null>(null)

/** 已答完的轮次，用于展示上下文。 */
const answered = computed<InterviewQuestion[]>(
  () =>
    detail.value?.questions.filter((item) => item.answer !== null) ?? [],
)

/** 待回答的问题：最后一道未作答的。 */
const current = computed<InterviewQuestion | null>(() => {
  const questions = detail.value?.questions ?? []
  for (let i = questions.length - 1; i >= 0; i -= 1) {
    if (questions[i].answer === null) {
      return questions[i]
    }
  }
  return null
})

const session = computed(() => detail.value?.session ?? null)

/** 进度：已问 / 预算。追问也算在内，与后端口径一致。 */
const progress = computed(() => {
  const total = session.value?.max_questions ?? 0
  const done = answered.value.length
  return {
    done,
    total,
    percent: total ? Math.min(100, (done / total) * 100) : 0,
  }
})

const isCompleted = computed(
  () => session.value?.status === 'completed',
)

async function load() {
  loading.value = true
  error.value = ''

  try {
    detail.value = await interviewApi.detail(props.id)
  } catch (err) {
    error.value =
      err instanceof ApiError ? err.message : '加载面试失败'
  } finally {
    loading.value = false
  }
}

async function submitAnswer() {
  const question = current.value
  const text = answerText.value.trim()

  if (!question || !text || submitting.value) {
    return
  }

  submitting.value = true
  error.value = ''

  try {
    const result = await interviewApi.answer(
      props.id,
      question.id,
      text,
    )

    answerText.value = ''

    // 预算耗尽：后端已自动完成评价，直接去报告页
    if (result.finished || !result.follow_up_question) {
      autoEvaluation.value = result.evaluation
      await load()

      if (result.evaluation || isCompleted.value) {
        await router.push({
          name: 'report',
          params: { id: props.id },
        })
        return
      }
    }

    await load()
  } catch (err) {
    if (err instanceof ApiError && err.isQuotaExceeded) {
      // 回答已保存，只是没能生成追问。刷新让界面与服务端一致。
      await load()
      error.value = `${err.message} 本次回答已保存，可稍后继续。`
    } else {
      error.value =
        err instanceof ApiError ? err.message : '提交失败，请重试'
    }
  } finally {
    submitting.value = false
  }
}

async function finish() {
  if (finishing.value) {
    return
  }

  finishing.value = true
  error.value = ''

  try {
    await interviewApi.finish(props.id)
    await router.push({
      name: 'report',
      params: { id: props.id },
    })
  } catch (err) {
    error.value =
      err instanceof ApiError ? err.message : '结束面试失败'
  } finally {
    finishing.value = false
  }
}

async function pause() {
  error.value = ''
  try {
    await interviewApi.pause(props.id)
    await load()
  } catch (err) {
    error.value =
      err instanceof ApiError ? err.message : '暂停失败'
  }
}

async function resume() {
  error.value = ''
  try {
    await interviewApi.resume(props.id)
    await load()
  } catch (err) {
    error.value =
      err instanceof ApiError ? err.message : '恢复失败'
  }
}

onMounted(async () => {
  await load()

  // 已经完成的会话不该停在面试页
  if (isCompleted.value) {
    await router.replace({
      name: 'report',
      params: { id: props.id },
    })
  }
})
</script>

<template>
  <div>
    <div v-if="error" class="alert alert-error">{{ error }}</div>

    <div v-if="loading" class="card">
      <span class="spinner" />正在加载面试…
    </div>

    <template v-else-if="session">
      <!-- 进度 -->
      <div class="card">
        <div class="row-between">
          <div>
            <h1>{{ session.target_role || '模拟面试' }}</h1>
            <p class="muted" style="margin: 0">
              第 {{ progress.done }} / {{ progress.total }} 题 ·
              <span class="tag">{{ session.status }}</span>
            </p>
          </div>

          <div class="row">
            <button
              v-if="session.status === 'paused'"
              class="btn-ghost"
              type="button"
              @click="resume"
            >
              继续面试
            </button>
            <button
              v-else
              class="btn-ghost"
              type="button"
              @click="pause"
            >
              暂停
            </button>
            <button
              class="btn-primary"
              type="button"
              :disabled="finishing || progress.done === 0"
              @click="finish"
            >
              <span v-if="finishing" class="spinner" />
              结束并生成报告
            </button>
          </div>
        </div>

        <div class="progress-track" style="margin-top: 12px">
          <div
            class="progress-fill"
            :style="{ width: progress.percent + '%' }"
          />
        </div>
      </div>

      <div v-if="session.status === 'paused'" class="alert alert-warn">
        面试已暂停
        <template v-if="session.pause_reason">
          （{{ session.pause_reason }}）
        </template>
        。点"继续面试"回到暂停前的进度。
      </div>

      <!-- 当前问题 -->
      <div v-if="current" class="card">
        <div class="row-between" style="margin-bottom: 12px">
          <h2>当前问题</h2>
          <span
            :class="
              current.is_general ? 'tag' : 'tag tag-primary'
            "
          >
            {{
              current.is_general
                ? '通用能力题'
                : `资料依据 ${current.evidence_chunk_ids.length} 段`
            }}
          </span>
        </div>

        <div class="question-box">{{ current.question }}</div>

        <div class="field" style="margin-top: 16px">
          <label for="answer">你的回答</label>
          <textarea
            id="answer"
            v-model="answerText"
            :disabled="submitting || session.status === 'paused'"
            placeholder="尽量给出具体做法：用了什么、参数取值、为什么这样选、遇到过什么问题。"
          />
        </div>

        <div class="row" style="margin-top: 14px">
          <button
            class="btn-primary"
            type="button"
            :disabled="
              submitting ||
              !answerText.trim() ||
              session.status === 'paused'
            "
            @click="submitAnswer"
          >
            <span v-if="submitting" class="spinner" />
            {{ submitting ? '正在分析并追问…' : '提交回答' }}
          </button>
          <span v-if="submitting" class="faint">
            需要调用模型分析回答，通常十几秒。
          </span>
        </div>
      </div>

      <div v-else class="card">
        <p class="muted" style="margin: 0">
          当前没有待回答的问题。
        </p>
        <div class="row" style="margin-top: 12px">
          <button class="btn-ghost" type="button" @click="load">
            刷新
          </button>
          <button
            class="btn-primary"
            type="button"
            :disabled="finishing"
            @click="finish"
          >
            结束并生成报告
          </button>
        </div>
      </div>

      <!-- 已答记录 -->
      <div v-if="answered.length" class="card">
        <h2>已完成的问答</h2>

        <div
          v-for="item in answered"
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
          </div>
          <div style="font-weight: 500">{{ item.question }}</div>
          <div class="muted" style="margin-top: 4px">
            {{ item.answer }}
          </div>
        </div>
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
