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
import { statusLabel } from '../api/labels'
import PageState from '../components/PageState.vue'
import WaitingIndicator from '../components/WaitingIndicator.vue'
import type {
  InterviewDetail,
  InterviewEvaluation,
  InterviewQuestion,
} from '../api/types'
import EvidencePanel from '../components/EvidencePanel.vue'

const props = defineProps<{ id: number }>()

const router = useRouter()

const detail = ref<InterviewDetail | null>(null)
const answerText = ref('')
const loading = ref(true)
const submitting = ref(false)
const finishing = ref(false)
const error = ref('')

/** 当前展开了资料依据的问题 id（同时只开一个，避免刷屏）。 */
const openEvidenceId = ref<number | null>(null)

function toggleEvidence(questionId: number) {
  openEvidenceId.value =
    openEvidenceId.value === questionId ? null : questionId
}

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

/**
 * 会话是否卡在"正在评价"状态。
 *
 * ## 为什么需要专门处理（实测缺陷 D76）
 *
 * "结束并生成报告"会先把会话推进到 `evaluating`，再调模型做整场评价。
 * 若评价失败（上游超时、限流），会话就**停在 evaluating** ——
 * 而这在设计上是**可重试**的（`finish_interview` 对 evaluating
 * 直接放行，见 ADR-025）。
 *
 * 但界面此前完全没有区分这个状态：
 *
 * - 它照样显示"当前问题"和回答输入框
 * - 用户以为还能继续答，输入并提交
 * - 后端返回 **409 原始状态机错误**：
 *   「非法状态转移：evaluating → waiting_for_answer；
 *     允许的目标状态为 ['asking','failed','paused','summarizing']」
 *
 * 实测库里有 2 个会话这样卡了 5 天。用户看到的是
 * "我明明能答题，为什么提交就报错" —— 而真正该做的是
 * **重试生成报告**，界面却从没告诉他。
 *
 * 因此：evaluating 状态要**屏蔽回答区**，只留"重试生成报告"。
 */
const isEvaluating = computed(
  () => session.value?.status === 'evaluating',
)

/**
 * 暂停后的恢复上下文（§10.6）。
 *
 * ## 它解决什么问题
 *
 * 原来的提示只说"面试已暂停（原因）。点'继续面试'回到暂停前的进度。"
 * —— 而用户**看不到"暂停前的进度"是什么**，得自己往上翻。
 *
 * 实测场景：一个用户隔了一天回来，看到"已暂停"三个字，
 * 不知道自己是"还有一题没答"还是"答案刚提交、在等下一题"。
 * 那决定了他点"继续"之后该做什么 —— 而界面没说。
 *
 * ## 为什么在前端算，而不是新增 API
 *
 * 所需数据（`questions` 的作答状态、`max_questions`、`questions_asked`）
 * **都已经在这个响应里**。为它多开一个端点会增加一条需要维护的契约，
 * 而收益为零。
 *
 * ⚠️ 但"恢复到哪个状态"的**规则**不在这里 —— 那属于后端状态机
 * （`resolve_resume_target`）：`resume_status` 缺失时它按问答事实
 * 推断，推断错了还有 `allowed_transitions` 兜底。
 * 前端只描述事实，不复制规则。
 */
const pausedContext = computed(() => {
  const data = session.value

  if (!data || data.status !== 'paused') {
    return null
  }

  const done = answered.value.length
  const pending = current.value

  return {
    done,
    total: data.max_questions,
    /** 是否有一道题已经问出来、但还没答 */
    hasPendingQuestion: pending !== null,
    pendingIndex: pending ? pending.question_index + 1 : null,
    /** 是否一题都还没问（异常情况，例如开完就暂停） */
    notStartedYet: done === 0 && pending === null,
  }
})

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
    <template v-if="loading">
      <PageState
        :error="error"
        loading-text="正在加载面试…"
      />
    </template>

    <!--
      ⚠️ `v-else-if` 必须挂在这个 `<template>` 上，而不是挂在
      `<PageState>` 上。实测踩过：把 `v-else-if` 挂在组件上会让
      Vue 的模板类型收窄失效 —— 后面的 `session.xxx` 全部报
      `TS18047: 'session' is possibly 'null'`，因为编译器不再知道
      这条分支里 `session` 非空。

      因此约定：**分支条件留在原生元素/`<template>` 上，
      组件只负责渲染它被给到的东西。**
    -->
    <template v-else-if="session">
      <!-- 进度 -->
      <div class="card">
        <div class="row-between">
          <div>
            <h1>{{ session.target_role || '模拟面试' }}</h1>
            <p class="muted" style="margin: 0">
              第 {{ progress.done }} / {{ progress.total }} 题 ·
              <!--
                ⚠️ 必须走 `statusLabel`，不能直接插 `session.status`。
                实测：原代码写的是 `{{ session.status }}`，
                于是界面上出现英文 `paused` ——
                而历史页与项目页早就各自做过中文映射。
              -->
              <span class="tag">{{ statusLabel(session.status) }}</span>
            </p>
          </div>

          <div class="row">
            <!--
              `evaluating` 状态不显示"暂停"。
              那个状态下"暂停"也没有意义（面试已经结束了流程），
              而显示它会让用户以为还能继续。
            -->
            <template v-if="!isEvaluating">
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
            </template>
            <button
              class="btn-primary"
              type="button"
              :disabled="finishing || progress.done === 0"
              @click="finish"
            >
              <span v-if="finishing" class="spinner" />
              <template v-if="finishing">正在生成报告…</template>
              <template v-else-if="isEvaluating">
                重试生成报告
              </template>
              <template v-else>结束并生成报告</template>
            </button>
          </div>

          <!-- 结束面试也要跑整场评价（实测约 10 秒），同样需要可见的等待 -->
          <WaitingIndicator
            :active="finishing"
            :steps="[
              '整理整场问答记录',
              '评价各维度表现',
              '生成优势与建议',
            ]"
            :reassure-after="30"
            :show-duration-hint="false"
          />
        </div>

        <div class="progress-track" style="margin-top: 12px">
          <div
            class="progress-fill"
            :style="{ width: progress.percent + '%' }"
          />
        </div>
      </div>

      <!--
        暂停提示：不只说"已暂停"，还要说清**停在哪、继续后做什么**。

        §10.6 的要求是"轨迹要能回答停在哪一步"，
        而原提示把这个问题留给了用户自己往上翻。
      -->
      <div v-if="session.status === 'paused'" class="alert alert-warn">
        <div>
          面试已暂停<template v-if="session.pause_reason">
            （{{ session.pause_reason }}）</template>
          <!--
            ⚠️ 中文标点前**不加空格**。
            实测截图里出现过 `暂停于 ...04 。` —— 一个空格夹在
            时间与中文句号之间，看起来像排版错误。
            多行模板里的换行会被 Vue 折叠成一个空格，
            因此句号必须紧跟在 `</template>` 后面写在同一行。
          -->
          <template v-if="session.paused_at">，暂停于
            {{ new Date(session.paused_at).toLocaleString() }}</template>。
        </div>

        <div v-if="pausedContext" style="margin-top: 6px">
          <!-- 进度：具体数字，不用"进度已保存"这种空话 -->
          <template v-if="pausedContext.done > 0">
            已答 {{ pausedContext.done }} /
            {{ pausedContext.total }} 题。
          </template>

          <!--
            最有用的那一句：告诉用户**继续之后会发生什么**。
            两种情况的界面表现完全不同，而用户看不出来：
            有未答题 → 回到那道题；没有 → 系统继续出下一题。
          -->
          <template v-if="pausedContext.hasPendingQuestion">
            第 {{ pausedContext.pendingIndex }} 题已问出但还没作答 ——
            点"继续面试"回到那道题。
          </template>
          <template v-else-if="pausedContext.notStartedYet">
            还没有出题 —— 点"继续面试"开始生成第一题
            （需要调用模型，通常十几秒）。
          </template>
          <template v-else>
            答案已全部提交 —— 点"继续面试"生成下一题
            （需要调用模型，通常十几秒）。
          </template>
        </div>
      </div>

      <!--
        评价未完成时的提示（D76）。

        这个状态此前完全被当成"正常面试中"：照样显示题目与回答框，
        用户提交后拿到的是原始状态机报错（409 非法状态转移）。
        实测库里有会话这样卡了 5 天。

        文案要说清三件事：发生了什么、数据还在不在、该点哪里。
      -->
      <div v-if="isEvaluating" class="alert alert-warn">
        <div>
          <strong>上次生成报告没有完成。</strong>
          面试记录与作答都已保存，只是整场评价那一步失败了
          （通常是上游模型服务临时不可用）。
        </div>
        <div style="margin-top: 6px">
          点右上角的「重试生成报告」即可重新评价，
          <strong>不会消耗面试额度</strong>（额度在开始面试时就已扣除）。
          在重试成功之前不能再提交回答 —— 这场面试已经结束了流程。
        </div>
      </div>

      <!-- 当前问题 -->
      <!--
        ⚠️ `evaluating` 时**不显示**回答区。
        那个状态下后端会拒绝作答（evaluating → waiting_for_answer 非法），
        而显示一个"能输入但提交必失败"的表单，
        比不显示更糟：用户会以为是自己操作错了。
      -->
      <div v-if="current && !isEvaluating" class="card">
        <div class="row-between" style="margin-bottom: 12px">
          <h2>当前问题</h2>
          <!--
            资料依据做成可展开：只显示"N 段"等于让用户相信一个数字，
            而 §9.3 要求依据**可追溯** —— 用户应该能亲眼看到
            这道题是从他哪段经历里来的。
          -->
          <button
            v-if="!current.is_general"
            class="evidence-toggle"
            type="button"
            @click="toggleEvidence(current.id)"
          >
            资料依据 {{ current.evidence_chunk_ids.length }} 段
            {{ openEvidenceId === current.id ? '▲' : '▼' }}
          </button>
          <span v-else class="tag">通用能力题</span>
        </div>

        <div class="question-box">{{ current.question }}</div>

        <EvidencePanel
          v-if="openEvidenceId === current.id"
          :session-id="id"
          :question-id="current.id"
        />

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
        </div>

        <!--
          长等待提示。
          此前这里只有一行静态文案（"通常十几秒"），
          而实测耗时 4.9s ~ 60.8s —— 用户看不出是等待还是卡死。
          见 `WaitingIndicator.vue` 头部关于"不假装知道服务端进度"的说明。
        -->
        <WaitingIndicator :active="submitting" />
      </div>

      <!--
        ⚠️ 必须是 `v-else-if="!isEvaluating"`，不能只用 `v-else`。

        否则 `evaluating` 状态会落到这个分支，显示
        "当前没有待回答的问题" —— 而实际上**是有**的，
        只是我上面刻意隐藏了回答区。那会给出错误的解释。
      -->
      <div v-else-if="!isEvaluating" class="card">
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
              通用题
            </span>
          </div>
          <div style="font-weight: 500">{{ item.question }}</div>

          <EvidencePanel
            v-if="openEvidenceId === item.id"
            :session-id="id"
            :question-id="item.id"
          />

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
