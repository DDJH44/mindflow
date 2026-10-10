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
import type { AbilityProfile, Project } from '../api/types'
import Sparkline from '../components/Sparkline.vue'

const profile = ref<AbilityProfile | null>(null)
const projects = ref<Project[]>([])
const loading = ref(true)
const error = ref('')

const selectedProjectId = ref<number | null>(null)

const hasData = computed(
  () => (profile.value?.session_count ?? 0) > 0,
)

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

    <div v-if="error" class="alert alert-error">{{ error }}</div>

    <div v-if="loading" class="card">
      <span class="spinner" />正在加载…
    </div>

    <template v-else-if="profile">
      <!-- 项目筛选 -->
      <div class="card">
        <div class="row-wrap">
          <button
            class="btn-ghost"
            type="button"
            :disabled="selectedProjectId === null"
            @click="filterByProject(null)"
          >
            全部项目
          </button>
          <button
            v-for="project in projects"
            :key="project.id"
            class="btn-ghost"
            type="button"
            :disabled="selectedProjectId === project.id"
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
