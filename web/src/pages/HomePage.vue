<script setup lang="ts">
/**
 * 首页：额度提示 → 选项目 → 新建并开始面试。
 *
 * 三件事都放在一屏，因为这是同一个决策：
 * "我还有额度吗、给哪个项目、开多长"。
 * 拆成多个页面会让用户看不到额度就开始点。
 */
import { computed, onMounted, ref } from 'vue'
import { useRouter } from 'vue-router'

import { interviewApi, projectApi, usageApi } from '../api'
import { ApiError } from '../api/http'
import type { Project, Usage } from '../api/types'

const router = useRouter()

const projects = ref<Project[]>([])
const usage = ref<Usage | null>(null)

const loading = ref(true)
const starting = ref(false)
const error = ref('')

const selectedProjectId = ref<number | null>(null)
const targetRole = ref('')
const maxQuestions = ref(8)

const newProjectName = ref('')
const creatingProject = ref(false)
const showProjectForm = ref(false)

/**
 * 额度提示文案。
 *
 * 由后端给出的 `limited_by` 决定说哪一句 ——
 * 前端不自己比较两个剩余量（比较规则只应存在于服务端，§25.5）。
 */
const quotaHint = computed(() => {
  if (!usage.value) {
    return ''
  }

  const { interviews, questions, limited_by: limitedBy } = usage.value

  if (limitedBy === 'interviews') {
    return `本月面试场次已用完（${interviews.used} / ${interviews.quota}）。`
  }

  if (limitedBy === 'questions') {
    return `本月题目额度已用完（${questions.used} / ${questions.quota}），暂时无法继续出题。`
  }

  return `本月剩余 ${interviews.remaining} 场（额度 ${interviews.quota}）、${questions.remaining} 题（额度 ${questions.quota}）。`
})

const canStart = computed(() => Boolean(usage.value?.allowed))

async function load() {
  loading.value = true
  error.value = ''

  try {
    const [projectList, currentUsage] = await Promise.all([
      projectApi.list(),
      usageApi.current(),
    ])

    projects.value = projectList
    usage.value = currentUsage

    if (!selectedProjectId.value && projectList.length) {
      selectedProjectId.value = projectList[0].id
    }
  } catch (err) {
    error.value =
      err instanceof ApiError ? err.message : '加载失败，请重试'
  } finally {
    loading.value = false
  }
}

async function createProject() {
  const name = newProjectName.value.trim()
  if (!name || creatingProject.value) {
    return
  }

  creatingProject.value = true
  error.value = ''

  try {
    const project = await projectApi.create(name)
    projects.value = [project, ...projects.value]
    selectedProjectId.value = project.id
    newProjectName.value = ''
    showProjectForm.value = false
  } catch (err) {
    error.value =
      err instanceof ApiError ? err.message : '创建项目失败'
  } finally {
    creatingProject.value = false
  }
}

/**
 * 新建会话并开始面试。
 *
 * 两步是**有意分开**的：
 * `create` 只产生一个 draft 草稿（不扣额度），
 * `start` 才真正调模型生成首题（扣一场额度）。
 * 若某一步失败，留在草稿上是可恢复的 —— 用户可以回首页重来。
 */
async function startInterview() {
  if (starting.value || !selectedProjectId.value) {
    return
  }

  starting.value = true
  error.value = ''

  try {
    const session = await interviewApi.create({
      project_id: selectedProjectId.value,
      target_role: targetRole.value.trim() || null,
      max_questions: maxQuestions.value,
    })

    await interviewApi.start(session.id)

    await router.push({
      name: 'interview',
      params: { id: session.id },
    })
  } catch (err) {
    if (err instanceof ApiError && err.isQuotaExceeded) {
      // 额度用尽要刷新额度显示，让提示与实际一致
      await load()
      error.value = err.message
    } else {
      error.value =
        err instanceof ApiError ? err.message : '开始面试失败'
    }
  } finally {
    starting.value = false
  }
}

onMounted(load)
</script>

<template>
  <div>
    <div class="page-head">
      <h1>开始一场面试</h1>
      <p class="muted">
        选择项目与目标岗位，系统会依据你上传的资料生成问题并逐轮追问。
      </p>
    </div>

    <div v-if="error" class="alert alert-error">{{ error }}</div>

    <div v-if="loading" class="card">
      <span class="spinner" />正在加载…
    </div>

    <template v-else>
      <!-- 额度 -->
      <div
        class="alert"
        :class="canStart ? 'alert-info' : 'alert-warn'"
      >
        {{ quotaHint }}
      </div>

      <!-- 项目 -->
      <div class="card">
        <div class="row-between">
          <h2>项目</h2>
          <button
            class="link"
            type="button"
            @click="showProjectForm = !showProjectForm"
          >
            {{ showProjectForm ? '取消' : '新建项目' }}
          </button>
        </div>

        <div v-if="showProjectForm" class="row" style="margin-bottom: 12px">
          <input
            v-model.trim="newProjectName"
            placeholder="项目名称，例如：Python 后端工程师面试"
            maxlength="100"
            @keyup.enter="createProject"
          />
          <button
            class="btn-ghost"
            type="button"
            :disabled="creatingProject || !newProjectName.trim()"
            @click="createProject"
          >
            <span v-if="creatingProject" class="spinner" />
            创建
          </button>
        </div>

        <div v-if="!projects.length" class="empty">
          还没有项目。先新建一个，再上传简历等资料。
        </div>

        <div v-else class="list" style="margin-top: 0">
          <div
            v-for="project in projects"
            :key="project.id"
            class="list-item"
            :class="{ active: project.id === selectedProjectId }"
            @click="selectedProjectId = project.id"
          >
            <div>
              <div>{{ project.name }}</div>
              <div v-if="project.description" class="faint">
                {{ project.description }}
              </div>
            </div>
            <span
              v-if="project.id === selectedProjectId"
              class="tag tag-primary"
            >
              已选择
            </span>
          </div>
        </div>

        <div class="row" style="margin-top: 14px">
          <RouterLink
            v-if="selectedProjectId"
            class="link"
            :to="{
              name: 'project',
              params: { id: selectedProjectId },
            }"
          >
            管理资料（上传简历 / JD / 项目材料）
          </RouterLink>
          <span v-else class="faint">先选择一个项目</span>
        </div>

        <p class="faint" style="margin: 8px 0 0">
          面试会依据资料出题。若项目下没有<strong>已索引</strong>的资料，
          问题会退化为通用题 —— 在资料页可以看到每份文件是否已索引。
        </p>
      </div>

      <!-- 面试设置 -->
      <div class="card">
        <h2>面试设置</h2>

        <div class="grid-2">
          <div>
            <label for="target-role">目标岗位（可选）</label>
            <input
              id="target-role"
              v-model.trim="targetRole"
              maxlength="100"
              placeholder="例如：后端工程师"
            />
            <p class="faint" style="margin: 6px 0 0">
              留空时用面试类型作为检索方向。
            </p>
          </div>

          <div>
            <label for="max-questions">题目数量上限</label>
            <input
              id="max-questions"
              v-model.number="maxQuestions"
              type="number"
              min="1"
              max="30"
            />
            <p class="faint" style="margin: 6px 0 0">
              含追问。达到上限会自动结束并给出评价。
            </p>
          </div>
        </div>

        <div class="row" style="margin-top: 18px">
          <button
            class="btn-primary"
            type="button"
            :disabled="
              starting || !selectedProjectId || !canStart
            "
            @click="startInterview"
          >
            <span v-if="starting" class="spinner" />
            {{ starting ? '正在生成第一题…' : '开始面试' }}
          </button>
          <span v-if="starting" class="faint">
            生成问题需要调用模型，通常需要十几秒。
          </span>
          <span v-else-if="!canStart" class="faint">
            本月额度已用完，下个账期重置。
          </span>
        </div>
      </div>
    </template>
  </div>
</template>
