<script setup lang="ts">
/**
 * 能力画像页（Phase 6）。
 *
 * **这一页最重要的设计约束：不能把评分波动说成能力进步。**
 *
 * 本项目实测同一份回答重复评分的极差有 10–20 分（§8A.9），
 * ADR-015 也明确"不用 LLM 评分均值衡量模型升级效果"。
 * 因此这一页刻意：
 *
 * - 给**中位数 + 区间 + 极差**，不给一个光洁的平均分
 * - 把服务端返回的 `caveats` **原样展示**在显眼位置
 * - 样本不足时（<3 场）不给出"优势 / 短板"这类判断
 * - 弱点只列原文，不做自动归类（归类会编造不存在的共性）
 */
import { computed, onMounted, ref } from 'vue'

import { interviewApi, projectApi } from '../api'
import { ApiError } from '../api/http'
import type {
  AbilityProfile,
  NextStep,
  Project,
} from '../api/types'
import { useFocusedInterview } from '../composables/useFocusedInterview'
import PageState from '../components/PageState.vue'
import Sparkline from '../components/Sparkline.vue'

const profile = ref<AbilityProfile | null>(null)
const projects = ref<Project[]>([])
const loading = ref(true)
const error = ref('')

const selectedProjectId = ref<number | null>(null)

const hasData = computed(
  () => (profile.value?.session_count ?? 0) > 0,
)

/**
 * 「下一步」建议 —— 与首页同一个接口、同一份服务端判断
 * （含"少于 3 场不下短板结论"），按当前项目筛选取数。
 *
 * 放在画像页的原因：用户在这里看到弱项时最想立刻练 ——
 * "看完弱项 → 跳首页 → 自己配置一场"是两步浪费。
 */
const nextStep = ref<NextStep | null>(null)

const { starting, startError, startFocused } = useFocusedInterview()

/** 画像页的"再面一场"是否可直接开始（需要具体项目）。 */
const canStartFromProfile = computed(
  () =>
    nextStep.value?.action === 'focus_next' &&
    selectedProjectId.value !== null,
)

async function loadNextStep() {
  try {
    nextStep.value = await interviewApi.nextStep(
      selectedProjectId.value ?? undefined,
    )
  } catch {
    // 建议卡只是引导，取不到就不显示。
    nextStep.value = null
  }
}

async function startFocusedFromProfile() {
  if (selectedProjectId.value === null) {
    return
  }

  await startFocused({
    projectId: selectedProjectId.value,
    query: nextStep.value?.query ?? null,
  })
}

function formatDate(value: string): string {
  const date = new Date(value)
  if (Number.isNaN(date.getTime())) {
    return value
  }
  return date.toLocaleDateString()
}

async function load() {
  loading.value = true
  error.value = ''

  try {
    const [projectList, result] = await Promise.all([
      projectApi.list(),
      interviewApi.abilityProfile(
        selectedProjectId.value ?? undefined,
      ),
    ])
    projects.value = projectList
    profile.value = result
  } catch (err) {
    error.value =
      err instanceof ApiError ? err.message : '加载失败'
  } finally {
    loading.value = false
  }

  // 建议卡单独取：它失败不影响画像本身。
  // （切换项目筛选时 load 会重跑，建议随之刷新。）
  await loadNextStep()
}

async function filterByProject(projectId: number | null) {
  selectedProjectId.value = projectId
  await load()
}

onMounted(load)
</script>

<template>
  <div>
    <div class="page-head">
      <div class="row-between">
        <div>
          <h1>能力画像</h1>
          <p class="muted" style="margin: 0">
            把多场面试的评价放在一起看趋势。
          </p>
        </div>
        <RouterLink class="link" :to="{ name: 'history' }">
          面试历史
        </RouterLink>
      </div>
    </div>

    <template v-if="loading">
      <PageState :error="error" />
    </template>

    <template v-else-if="profile">
      <!-- 项目筛选 -->
      <div class="card">
        <div class="row-wrap">
          <!--
            ⚠️ 用 `chip` + `is-active`，**不用 `:disabled`**。

            旧实现把"当前选中"表达成 `:disabled="selected === id"`，
            这有两个问题：

            ① `disabled` 会命中 `button:disabled { opacity: .5 }`
               —— 于是**当前选中的按钮反而是最模糊的那个**，
               用户第一眼会以为它不可用。
            ② `disabled` 的按钮不可点、也不可聚焦（键盘不可达）。
               而"点一下当前项"本该是幂等的无操作，不是禁用。

            现在用 `aria-pressed` 表达选中语义 ——
            那才是给读屏器的正确信息，视觉则用橙色左侧条 + 浅底。
          -->
          <button
            class="chip"
            :class="{ 'is-active': selectedProjectId === null }"
            type="button"
            :aria-pressed="selectedProjectId === null"
            @click="filterByProject(null)"
          >
            全部项目
          </button>
          <button
            v-for="project in projects"
            :key="project.id"
            class="chip"
            :class="{ 'is-active': selectedProjectId === project.id }"
            type="button"
            :aria-pressed="selectedProjectId === project.id"
            @click="filterByProject(project.id)"
          >
            {{ project.name }}
          </button>
          <span class="faint">
            已统计 {{ profile.session_count }} 场面试
          </span>
        </div>
      </div>

      <div v-if="!hasData" class="card">
        <p class="muted" style="margin: 0">
          还没有已完成的面试评价。完成一场面试后这里会显示能力趋势。
        </p>
        <div class="row" style="margin-top: 12px">
          <RouterLink class="link" :to="{ name: 'home' }">
            去开一场面试
          </RouterLink>
        </div>
      </div>

      <template v-else>
        <!--
          解读说明放在**最前面**，不放页脚。
          用户会先看分数再看结论；把"这不是精确测量"放在最后，
          等于让他先形成错误印象。
        -->
        <div
          v-for="(caveat, index) in profile.caveats"
          :key="index"
          class="alert alert-warn"
          :style="index ? 'margin-top: 8px' : ''"
        >
          {{ caveat }}
        </div>

        <!--
          「下一步」建议 —— 与首页同一接口、同一判断。

          放在维度卡片**之前**：用户在画像页的核心诉求是
          "我接下来练什么"，维度数据是它的依据 —— 行动先于依据
          被看到（与首页"建议在额度之上"同一逻辑）。

          画像页特有的约束：「再面一场」需要具体项目，而画像
          默认聚合**全部项目** —— 未选项目时按钮降级为提示，
          而不是替用户猜一个项目。
        -->
        <div
          v-if="nextStep && nextStep.action !== 'start_first'"
          class="card next-step"
          :class="{
            'next-step--accent':
              nextStep.action === 'focus_next',
          }"
        >
          <h2 style="margin: 0">{{ nextStep.title }}</h2>

          <p
            v-if="nextStep.reason"
            class="muted"
            style="margin: 8px 0 0"
          >
            {{ nextStep.reason }}
          </p>

          <div
            v-if="nextStep.focus.length"
            class="row-wrap"
            style="margin-top: 10px; gap: 8px"
          >
            <span class="faint">下一场会聚焦：</span>
            <span
              v-for="item in nextStep.focus"
              :key="item"
              class="tag tag-accent"
            >
              {{ item }}
            </span>
          </div>

          <div class="row" style="margin-top: 14px">
            <button
              v-if="nextStep.action === 'focus_next'"
              class="btn-primary"
              type="button"
              :disabled="starting || !canStartFromProfile"
              @click="startFocusedFromProfile"
            >
              <span v-if="starting" class="spinner" />
              {{ starting ? '正在生成第一题…' : '针对这些再面一场' }}
            </button>

            <span
              v-if="
                nextStep.action === 'focus_next' &&
                  selectedProjectId === null
              "
              class="faint"
            >
              在上方选择一个项目后即可开始。
            </span>

            <RouterLink
              v-else-if="nextStep.action === 'add_materials'"
              class="link"
              :to="{ name: 'home' }"
            >
              先上传资料
            </RouterLink>
          </div>

          <p
            v-if="startError"
            class="alert alert-warn"
            style="margin: 10px 0 0"
          >
            {{ startError }}
          </p>
        </div>

        <!-- 维度卡片 -->
        <div class="card">
          <h2>各维度</h2>

          <div class="dimension-grid">
            <div
              v-for="dimension in profile.dimensions"
              :key="dimension.key"
              class="dimension-cell"
            >
              <div class="row-between">
                <span class="dimension-label">
                  {{ dimension.label }}
                </span>
                <span
                  v-if="dimension.spread !== null"
                  class="tag"
                  :class="
                    dimension.spread >= 10 ? 'tag-warn' : ''
                  "
                >
                  极差 {{ dimension.spread }}
                </span>
              </div>

              <div class="dimension-figure">
                <span class="dimension-median">
                  {{ dimension.median ?? '—' }}
                </span>
                <span class="faint">中位数</span>
              </div>

              <div class="faint">
                区间 {{ dimension.minimum }}–{{ dimension.maximum }}
                · 最近 {{ dimension.latest }}
              </div>

              <Sparkline
                :values="dimension.history"
                :label="dimension.label"
                class="dimension-spark"
              />

              <div class="faint">
                共 {{ dimension.count }} 次测量
              </div>
            </div>
          </div>

          <p class="faint" style="margin-top: 10px">
            「极差」是最高分与最低分之差。面试分数有采样波动，
            单次高低不构成能力判断 —— 请结合极差与测量次数一起看。
          </p>
        </div>

        <!-- 重复出现的弱点 -->
        <div v-if="profile.recurring_weaknesses.length" class="card">
          <h2>反复出现的问题</h2>
          <p class="faint" style="margin-top: 0">
            这些是<strong>多场面试里原文相同</strong>的不足。只列原文，
            不做自动归类 —— 归类会编造出不存在的共性。
          </p>
          <ul class="bullet-list">
            <li
              v-for="item in profile.recurring_weaknesses"
              :key="item.text"
            >
              <span class="tag tag-warn">
                出现 {{ item.occurrences }} 次
              </span>
              {{ item.text }}
            </li>
          </ul>
        </div>

        <!-- 最近的问题与建议 -->
        <div
          v-if="
            profile.recent_weaknesses.length ||
            profile.recent_suggestions.length
          "
          class="card"
        >
          <h2>最近的问题与建议</h2>

          <div v-if="profile.recent_weaknesses.length">
            <h3>需要留意的不足</h3>
            <ul class="bullet-list">
              <li
                v-for="(item, index) in profile.recent_weaknesses"
                :key="index"
              >
                {{ item }}
              </li>
            </ul>
          </div>

          <div
            v-if="profile.recent_suggestions.length"
            :style="
              profile.recent_weaknesses.length
                ? 'margin-top: 14px'
                : ''
            "
          >
            <h3>改进建议</h3>
            <ul class="bullet-list">
              <li
                v-for="(item, index) in profile.recent_suggestions"
                :key="index"
              >
                {{ item }}
              </li>
            </ul>
          </div>
        </div>

        <!-- 逐场明细 -->
        <div class="card">
          <h2>逐场明细</h2>

          <div class="table-scroll">
            <table class="data-table">
              <thead>
                <tr>
                  <th>时间</th>
                  <th>项目</th>
                  <th>岗位</th>
                  <th
                    v-for="dimension in profile.dimensions"
                    :key="dimension.key"
                  >
                    {{ dimension.label }}
                  </th>
                  <th></th>
                </tr>
              </thead>
              <tbody>
                <tr
                  v-for="item in [...profile.sessions].reverse()"
                  :key="`${item.session_id}-${item.evaluated_at}`"
                >
                  <td>{{ formatDate(item.evaluated_at) }}</td>
                  <td>{{ item.project_name ?? '—' }}</td>
                  <td>{{ item.target_role ?? '—' }}</td>
                  <td
                    v-for="dimension in profile.dimensions"
                    :key="dimension.key"
                  >
                    {{ item.scores[dimension.key] ?? '—' }}
                  </td>
                  <td>
                    <RouterLink
                      class="link"
                      :to="{
                        name: 'report',
                        params: { id: item.session_id },
                      }"
                    >
                      报告
                    </RouterLink>
                  </td>
                </tr>
              </tbody>
            </table>
          </div>

          <p class="faint" style="margin-top: 8px">
            最近的在最上面。
          </p>
        </div>
      </template>
    </template>
  </div>
</template>
