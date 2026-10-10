<script setup lang="ts">
/**
 * 资料依据面板。
 *
 * 存在的理由：§9.3 要求资料型问题**可追溯依据**，但此前接口只给出
 * 一串 `evidence_chunk_ids`，用户看不到"这道题是从我哪段资料来的"——
 * 数字本身建立不了信任。人才会想知道"系统是不是真读了我的简历"。
 *
 * 抽成组件是因为面试页与报告页都要用：那两处若各写一遍取数、
 * 加载态、错误态与失效提示，很快会漂移成两套行为。
 */
import { onMounted, ref } from 'vue'

import { interviewApi } from '../api'
import { ApiError } from '../api/http'
import type { InterviewEvidence } from '../api/types'

const props = defineProps<{
  sessionId: number
  questionId: number
}>()

const evidence = ref<InterviewEvidence | null>(null)
const loading = ref(false)
const error = ref('')

/**
 * 只在**首次展开时**加载。
 *
 * 报告页可能列出十几道题，如果每道题都自动请求一次，
 * 打开报告会产生一串用不到的请求。
 */
const loaded = ref(false)

async function load() {
  if (loaded.value || loading.value) {
    return
  }

  loading.value = true
  error.value = ''

  try {
    evidence.value = await interviewApi.evidence(
      props.sessionId,
      props.questionId,
    )
    loaded.value = true
  } catch (err) {
    error.value =
      err instanceof ApiError ? err.message : '加载依据失败'
  } finally {
    loading.value = false
  }
}

/** 资料类型的中文名，与资料页保持同一套叫法。 */
function typeLabel(value: string | null): string {
  if (!value) {
    return ''
  }
  return (
    {
      resume: '简历',
      jd: '岗位 JD',
      project: '项目材料',
      code: '代码片段',
      other: '其他',
    }[value] ?? value
  )
}

onMounted(load)
</script>

<template>
  <div class="evidence">
    <div v-if="loading" class="faint">
      <span class="spinner" />正在读取依据…
    </div>

    <div v-else-if="error" class="alert alert-error">
      {{ error }}
    </div>

    <template v-else-if="evidence">
      <div
        v-if="evidence.is_general"
        class="faint"
        style="margin-bottom: 6px"
      >
        这是通用能力题，没有引用你的资料。
      </div>

      <div v-if="!evidence.items.length && !evidence.is_general" class="faint">
        没有可展示的依据。
      </div>

      <div
        v-for="(item, index) in evidence.items"
        :key="item.chunk_id"
        class="evidence-item"
      >
        <div class="evidence-head">
          <span class="faint">依据 {{ index + 1 }}</span>
          <span v-if="item.document_name" class="tag">
            {{ item.document_name
            }}<template v-if="item.document_type">
              · {{ typeLabel(item.document_type) }}</template>
          </span>
        </div>

        <p class="evidence-text">{{ item.content }}</p>
      </div>

      <!--
        失效依据必须明说。
        资料被删除后依据会取不到 —— 静默少几条会让用户以为
        "这道题本来就没依据"，而真相是数据已经不在了。
      -->
      <div
        v-if="evidence.missing_chunk_ids.length"
        class="alert alert-warn"
        style="margin-top: 8px"
      >
        有 {{ evidence.missing_chunk_ids.length }} 条依据引用的资料已被删除，
        无法显示内容。
      </div>
    </template>
  </div>
</template>
