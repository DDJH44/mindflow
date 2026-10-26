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
import type { FocusSuggestion, InterviewDetail } from '../api/types'
import { useFocusedInterview } from '../composables/useFocusedInterview'
import EvidencePanel from '../components/EvidencePanel.vue'
import TurnCitations from '../components/TurnCitations.vue'
import PageState from '../components/PageState.vue'

const props = defineProps<{ id: number }>()

const detail = ref<InterviewDetail | null>(null)
const loading = ref(true)
const error = ref('')

/** 当前展开了资料依据的问题 id。 */
const openEvidenceId = ref<number | null>(null)

/**
 * 结论 → 出处轮次。
 *
 * 后端**强制** `finding.text` 逐字取自 strengths/weaknesses/suggestions，
 * 因此这里可以精确匹配。**不要**改成模糊匹配：模糊匹配会把出处
 * 挂到错误的条目上，而那比没有出处更糟。
 */
const findingIndex = computed(() => {
  const map = new Map<string, number[]>()

  for (const finding of detail.value?.evaluation?.findings ?? []) {
    if (!finding.verified || !finding.turns.length) {
      continue
    }

    map.set(`${finding.kind}::${finding.text.trim()}`, finding.turns)
  }

  return map
})

function turnsOf(
  kind: 'strength' | 'weakness' | 'suggestion',
  text: string,
): number[] {
  return findingIndex.value.get(`${kind}::${text.trim()}`) ?? []
}

/**
 * 跳到某一题的问答记录。
 *
 * 用 `scrollIntoView` 而不是锚点跳转：锚点跳转会把 URL 加上
 * `#q-3`，刷新后仍停在中间，而报告页本来只有一屏内容。
 */
function jumpToQuestion(turn: number) {
  const target = document.getElementById(`q-${turn}`)

  if (target) {
    target.scrollIntoView({ behavior: 'smooth', block: 'center' })
  }
}

function toggleEvidence(questionId: number) {
  openEvidenceId.value =
    openEvidenceId.value === questionId ? null : questionId
}

const evaluation = computed(() => detail.value?.evaluation ?? null)
const session = computed(() => detail.value?.session ?? null)
const questions = computed(() => detail.value?.questions ?? [])

/**
 * 下一场的聚焦方向（服务端从**本场**评价推导）。
 *
 * 与首页 `NextStep` 的区别：那边聚合多场、受"样本少不下结论"
 * 约束；这里只回看这一场 —— 报告页本来就展示了本场弱项，
 * "下一场重点考察它们"一场也成立。判断在服务端，前端不推导。
 */
const focusSuggestion = ref<FocusSuggestion | null>(null)

const { starting, startError, startFocused } = useFocusedInterview()

async function loadFocusSuggestion() {
  try {
    focusSuggestion.value = await interviewApi.focusSuggestion(props.id)
  } catch {
    // 聚焦建议只是引导，取不到就不显示 ——
    // 不该因为一个辅助接口失败而让报告页报错。
    focusSuggestion.value = null
  }
}

/**
 * 按本场的聚焦方向再开一场。
 *
 * 项目、岗位、题目上限**继承本场** —— "再面一场"的语义是
 * 同样的设置下换方向重考，而不是回到首页从零配置。
 */
async function startFocusedFromReport() {
  if (!session.value) {
    return
  }

  await startFocused({
    projectId: session.value.project_id,
    targetRole: session.value.target_role,
    maxQuestions: session.value.max_questions,
    query: focusSuggestion.value?.query ?? null,
  })
}

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

/**
 * 样本量提示。
 *
 * ## 为什么必须有
 *
 * 实测事故：一次面试**只答了 1 题**（回答是"不知道"），
 * 报告上写着「综合 15」，读起来像"你整体表现很差" ——
 * 而实际样本量是 1。
 *
 * 这违反了本项目的原则：**界面不得承诺系统做不到的事。**
 * 一份 1 题的评价不能以"整场结论"的口吻呈现。
 *
 * ## 判据为什么用"已作答题数"
 *
 * 后端评价的 rubric 里，`overall` / `technical` / `project`
 * 的档位都按"多数轮次"的口径描述。因此决定结论可靠性的
 * 是**有回答的轮数**，而不是"生成了几道题" ——
 * 生成了 8 题但只答了 1 题，结论的依据仍然只有 1 轮。
 */
const sampleSizeHint = computed(() => {
  const answeredCount = questions.value.filter(
    (item) => item.answer !== null,
  ).length

  if (answeredCount === 0) {
    return '本次没有有效作答，四个维度的分数仅表示"未提供信息"。'
  }

  if (answeredCount <= 2) {
    return `本次只有 ${answeredCount} 轮有效作答，样本量很少 —— 分数依据有限，不代表整体水平。`
  }

  return ''
})

/** 样本量是否不足（不足时用告警样式，避免被当成正常说明） */
const sampleSizeTooSmall = computed(() => {
  const answeredCount = questions.value.filter(
    (item) => item.answer !== null,
  ).length
  return answeredCount <= 2
})

/**
 * 逐轮分析的维度名 → 中文。
 *
 * 这些键来自后端 `AnswerAnalysis.anchor_summary()`。
 *
 * ⚠️ 未命中时**回退成原键名**，而不是空白：
 * 后端将来新增维度时，一个英文单词至少能让人搜到它，
 * 空白则会让人以为界面坏了。
 */
const ANCHOR_LABELS: Record<string, string> = {
  answer_quality: '回答质量',
  technical_depth: '技术深度',
  completeness: '完整性',
}

function anchorLabel(key: string): string {
  return ANCHOR_LABELS[key] ?? key
}

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

  // 聚焦建议单独取：它失败不影响报告本身。
  await loadFocusSuggestion()
}

onMounted(load)
</script>

<template>
  <div>
    <template v-if="loading">
      <PageState :error="error" loading-text="正在加载报告…" />
    </template>

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
        <!--
          样本量提示放在**分数上方**，而不是下方。
          
          实测事故：一次只答了 1 题（"不知道"）的面试，
          报告上写着「综合 15」，读起来像"整体很差"。
          放在下方会被忽略 —— 而它正是理解这四个数字的前提。
        -->
        <p
          v-if="sampleSizeHint"
          class="alert"
          :class="
            sampleSizeTooSmall ? 'alert-warn' : 'alert-info'
          "
          style="margin-bottom: 16px"
        >
          {{ sampleSizeHint }}
        </p>

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
              <TurnCitations
                :turns="turnsOf('strength', item)"
                @jump="jumpToQuestion"
              />
            </li>
          </ul>
        </div>

        <!-- 风险 -->
        <div v-if="evaluation.weaknesses.length" class="card">
          <h2>需要留意的不足</h2>
          <ul class="bullet-list">
            <li v-for="(item, index) in evaluation.weaknesses" :key="index">
              {{ item }}
              <TurnCitations
                :turns="turnsOf('weakness', item)"
                @jump="jumpToQuestion"
              />
            </li>
          </ul>
        </div>

        <!-- 建议 -->
        <div v-if="evaluation.suggestions.length" class="card">
          <h2>改进建议</h2>
          <ul class="bullet-list">
            <li v-for="(item, index) in evaluation.suggestions" :key="index">
              {{ item }}
              <TurnCitations
                :turns="turnsOf('suggestion', item)"
                @jump="jumpToQuestion"
              />
            </li>
          </ul>
        </div>

        <!--
          下一步：把这一场暴露的不足直接带进下一场。

          这是"报告 → ？"那一环在**报告页**的落点 —— 在此之前
          看完报告只能跳首页从零配置。项目 / 岗位 / 题目上限
          继承本场，聚焦方向由服务端从本场评价推导。

          没有评价、或辅助接口失败时 `query` 为空 → 整块不显示，
          报告页仍然完整。
        -->
        <div
          v-if="evaluation && focusSuggestion?.query"
          class="card next-step next-step--accent"
        >
          <h2 style="margin: 0">针对这场暴露的不足再面一场</h2>

          <div
            class="row-wrap"
            style="margin-top: 10px; gap: 8px"
          >
            <span class="faint">下一场会聚焦：</span>
            <span
              v-for="item in focusSuggestion.focus"
              :key="item"
              class="tag tag-accent"
            >
              {{ item }}
            </span>
          </div>

          <div class="row" style="margin-top: 14px">
            <button
              class="btn-primary"
              type="button"
              :disabled="starting"
              @click="startFocusedFromReport"
            >
              <span v-if="starting" class="spinner" />
              {{ starting ? '正在生成第一题…' : '再面一场' }}
            </button>
            <span v-if="starting" class="faint">
              生成问题需要调用模型，通常需要十几秒。
            </span>
          </div>

          <p
            v-if="startError"
            class="alert alert-warn"
            style="margin: 10px 0 0"
          >
            {{ startError }}
          </p>
        </div>
      </template>

      <!-- 问答记录 -->
      <div class="card">
        <h2>完整问答记录</h2>

        <div v-if="!questions.length" class="empty">
          没有记录到问答。
        </div>

        <!--
          每一题带 `id="q-N"`，供上面三组结论的「第 N 题」小标签跳转。
          N 用 `question_index + 1`，与展示文案、以及后端评价时拼的
          「第 N 轮」编号**保持同一口径** —— 三处不一致会让跳转落到
          另一道题上，而那种错误看起来像"出处标错了"。
        -->
        <div
          v-for="item in questions"
          :id="`q-${item.question_index + 1}`"
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

          <!--
            逐轮分析：这一题当时被判定"漏了什么"。

            ⚠️ 为什么放在报告页而不是作答过程中：
            面试中途把"技术深度偏低"给候选人看会干扰后续作答
            （`routers/interviews.py` 里有这条刻意的决定）。
            复盘时看才是学习价值。

            `item.analysis` 为 null 时整块不渲染 ——
            两种原因（未作答 / 历史记录）对用户结果相同，
            显示"无数据"只会让人以为坏了。
          -->
          <div v-if="item.analysis" class="answer-analysis">
            <div class="row-wrap" style="gap: 8px">
              <span class="faint">这一题当时被判定：</span>
              <span
                v-for="(anchor, key) in item.analysis.anchors"
                :key="key"
                class="tag"
              >
                {{ anchorLabel(key) }} {{ anchor || '—' }}
              </span>
            </div>

            <div
              v-if="item.analysis.missing_points.length"
              style="margin-top: 6px"
            >
              <span class="faint">
                可以补充的 {{ item.analysis.missing_points.length }} 点：
              </span>
              <span class="muted">
                {{ item.analysis.missing_points.join('、') }}
              </span>
            </div>
            <div v-else class="faint" style="margin-top: 6px">
              这一题没有明显缺口。
            </div>
          </div>
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
