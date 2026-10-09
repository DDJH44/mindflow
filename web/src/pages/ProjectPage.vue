<script setup lang="ts">
/**
 * 项目资料页。
 *
 * 这一页解决一个**静默的断点**：文档上传后必须写入向量索引
 * 才能被检索到。停在 `chunked` 时检索为空、面试只会出通用题，
 * 而用户完全看不出哪里不对。因此这里把状态如实展示，
 * 并给一个"重试索引"的动作。
 */
import { computed, onMounted, ref } from 'vue'

import { documentApi, projectApi } from '../api'
import { ApiError } from '../api/http'
import type {
  DocumentItem,
  DocumentType,
  Project,
} from '../api/types'

const props = defineProps<{ id: number }>()

const project = ref<Project | null>(null)
const documents = ref<DocumentItem[]>([])
const loading = ref(true)
const error = ref('')

const uploading = ref(false)
const uploadPercent = ref(0)
const uploadType = ref<DocumentType>('resume')
const retryingId = ref<number | null>(null)
const deletingId = ref<number | null>(null)

const TYPE_OPTIONS: { value: DocumentType; label: string }[] = [
  { value: 'resume', label: '简历' },
  { value: 'jd', label: '岗位 JD' },
  { value: 'project', label: '项目材料' },
  { value: 'code', label: '代码片段' },
  { value: 'other', label: '其他' },
]

/**
 * 与后端 `DocumentParser.SUPPORTED_SUFFIXES` 保持一致。
 *
 * 这个列表曾经与后端不一致：界面写着支持 PDF / DOCX，
 * 而后端只有 TXT / MD，用户传简历直接报"暂不支持的文件类型"。
 * 界面**不能承诺后端做不到的事**。
 */
const ACCEPTED_SUFFIXES = ['.txt', '.md', '.pdf', '.docx']
const ACCEPT_ATTR = ACCEPTED_SUFFIXES.join(',')
const MAX_FILE_MB = 20

/** 可被检索到的文档数。面试能不能出好题就取决于这个。 */
const indexedCount = computed(
  () => documents.value.filter((item) => item.status === 'embedded').length,
)

const hasUnindexed = computed(() =>
  documents.value.some((item) => item.status !== 'embedded'),
)

function statusLabel(status: string): string {
  return (
    {
      pending: '待处理',
      parsed: '已解析',
      chunked: '已切块·未索引',
      embedded: '已索引·可检索',
      failed: '处理失败',
    }[status] ?? status
  )
}

function statusClass(status: string): string {
  if (status === 'embedded') {
    return 'tag tag-ok'
  }
  if (status === 'failed') {
    return 'tag tag-warn'
  }
  if (status === 'chunked') {
    return 'tag tag-warn'
  }
  return 'tag'
}

function typeLabel(value: string): string {
  return (
    TYPE_OPTIONS.find((item) => item.value === value)?.label ?? value
  )
}

async function load() {
  loading.value = true
  error.value = ''

  try {
    const [detail, list] = await Promise.all([
      projectApi.detail(props.id),
      documentApi.list(props.id),
    ])
    project.value = detail
    documents.value = list
  } catch (err) {
    error.value =
      err instanceof ApiError ? err.message : '加载失败'
  } finally {
    loading.value = false
  }
}

async function upload(event: Event) {
  const input = event.target as HTMLInputElement
  const file = input.files?.[0]

  if (!file || uploading.value) {
    return
  }

  // 前端先做一次检查，为了**立刻给出反馈**。
  //
  // 这不是安全校验（后端仍会校验）—— 而是用户体验：
  // 不做的话，一个 20MB 的 PDF 要先完整传上去才被拒。
  const name = file.name.toLowerCase()
  const suffix = name.slice(name.lastIndexOf('.'))

  if (!ACCEPTED_SUFFIXES.includes(suffix)) {
    error.value =
      `不支持的文件类型 ${suffix || '（无扩展名）'}。` +
      `请上传 ${ACCEPTED_SUFFIXES.join(' / ')}。`
    input.value = ''
    return
  }

  if (file.size > MAX_FILE_MB * 1024 * 1024) {
    error.value =
      `文件过大（${(file.size / 1024 / 1024).toFixed(1)}MB），` +
      `上限 ${MAX_FILE_MB}MB。`
    input.value = ''
    return
  }

  uploading.value = true
  uploadPercent.value = 0
  error.value = ''

  try {
    const created = await documentApi.upload(
      props.id,
      file,
      uploadType.value,
      (percent) => {
        uploadPercent.value = percent
      },
    )
    documents.value = [created, ...documents.value]
  } catch (err) {
    if (err instanceof ApiError) {
      // 502 表示"文件已保存但索引失败" —— 文档其实已经入库了，
      // 因此要刷新列表让用户看到它，而不是以为上传白做了。
      if (err.status === 502) {
        await load()
      }
      error.value = err.message
    } else {
      error.value = '上传失败'
    }
  } finally {
    uploading.value = false
    uploadPercent.value = 0
    input.value = ''
  }
}

async function retryEmbed(document: DocumentItem) {
  if (retryingId.value !== null) {
    return
  }

  retryingId.value = document.id
  error.value = ''

  try {
    const updated = await documentApi.embed(props.id, document.id)
    documents.value = documents.value.map((item) =>
      item.id === updated.id ? updated : item,
    )
  } catch (err) {
    error.value =
      err instanceof ApiError ? err.message : '重试索引失败'
  } finally {
    retryingId.value = null
  }
}

async function removeDocument(document: DocumentItem) {
  if (deletingId.value !== null) {
    return
  }

  // 删除会连带清掉向量索引，且不可撤销 —— 先确认
  const confirmed = window.confirm(
    `删除「${document.name}」？\n\n这会同时删除它的向量索引，无法撤销。`,
  )
  if (!confirmed) {
    return
  }

  deletingId.value = document.id
  error.value = ''

  try {
    await documentApi.remove(props.id, document.id)
    documents.value = documents.value.filter(
      (item) => item.id !== document.id,
    )
  } catch (err) {
    error.value =
      err instanceof ApiError ? err.message : '删除失败'
  } finally {
    deletingId.value = null
  }
}

onMounted(load)
</script>
<template>
  <div>
    <div class="page-head">
      <div class="row-between">
        <div>
          <h1>{{ project?.name ?? '项目资料' }}</h1>
          <p class="muted" style="margin: 0">
            上传简历、JD、项目材料，系统会切块并建立向量索引，
            面试据此生成个性化问题。
          </p>
        </div>
        <RouterLink class="link" :to="{ name: 'home' }">
          返回首页
        </RouterLink>
      </div>
    </div>

    <div v-if="error" class="alert alert-error">{{ error }}</div>

    <div v-if="loading" class="card">
      <span class="spinner" />正在加载…
    </div>

    <template v-else>
      <!-- 索引状态：决定面试能否出好题 -->
      <div
        class="alert"
        :class="indexedCount > 0 ? 'alert-ok' : 'alert-warn'"
      >
        <template v-if="indexedCount > 0">
          已有 {{ indexedCount }} 份资料可被检索，面试会依据它们出题。
        </template>
        <template v-else>
          还没有可被检索的资料。<strong>上传并完成索引后</strong>，
          面试才会依据你的资料出题；否则只会问通用问题。
        </template>
      </div>

      <!-- 上传 -->
      <div class="card">
        <h2>上传资料</h2>

        <div class="grid-2">
          <div>
            <label for="doc-type">资料类型</label>
            <select id="doc-type" v-model="uploadType">
              <option
                v-for="item in TYPE_OPTIONS"
                :key="item.value"
                :value="item.value"
              >
                {{ item.label }}
              </option>
            </select>
            <p class="faint" style="margin: 6px 0 0">
              类型会影响检索过滤，选错会让资料参与错误的检索。
            </p>
          </div>

          <div>
            <label for="doc-file">文件</label>
            <input
              id="doc-file"
              type="file"
              :accept="ACCEPT_ATTR"
              :disabled="uploading"
              @change="upload"
            />
            <p class="faint" style="margin: 6px 0 0">
              支持 TXT / MD / PDF / DOCX，单个文件上限 {{ MAX_FILE_MB }}MB。
              上传后会解析、切块并建立索引，大文件可能需要一会儿。
            </p>
            <p class="faint" style="margin: 4px 0 0">
              提示：若简历是「图片型」的（正文其实是一张图），
              系统提取不到文字，需要先另存为文本型 PDF。
            </p>
          </div>
        </div>

        <div v-if="uploading" style="margin-top: 14px">
          <div class="row">
            <span class="spinner" />
            <span class="muted">
              正在上传并建立索引…（
              {{ uploadPercent }}%，解析与嵌入在后面进行）
            </span>
          </div>
          <div class="progress-track" style="margin-top: 8px">
            <div
              class="progress-fill"
              :style="{ width: uploadPercent + '%' }"
            />
          </div>
        </div>
      </div>

      <!-- 列表 -->
      <div class="card">
        <h2>已上传资料（{{ documents.length }}）</h2>

        <div v-if="!documents.length" class="empty">
          还没有上传任何资料。
        </div>

        <div v-else class="stack">
          <div
            v-for="item in documents"
            :key="item.id"
            class="row-between"
            style="
              padding: 12px 0;
              border-bottom: 1px solid var(--border);
            "
          >
            <div style="min-width: 0">
              <div
                style="
                  display: flex;
                  align-items: center;
                  gap: 8px;
                  flex-wrap: wrap;
                "
              >
                <span>{{ item.name }}</span>
                <span :class="statusClass(item.status)">
                  {{ statusLabel(item.status) }}
                </span>
                <span class="tag">{{ typeLabel(item.document_type) }}</span>
              </div>
              <div class="faint">
                {{ item.original_filename }} ·
                {{ new Date(item.created_at).toLocaleString() }}
              </div>
            </div>

            <div class="row" style="flex-shrink: 0">
              <button
                v-if="item.status !== 'embedded'"
                class="btn-ghost"
                type="button"
                :disabled="retryingId !== null || deletingId !== null"
                @click="retryEmbed(item)"
              >
                <span v-if="retryingId === item.id" class="spinner" />
                重试索引
              </button>
              <button
                class="btn-ghost"
                type="button"
                style="color: var(--danger)"
                :disabled="deletingId !== null || retryingId !== null"
                @click="removeDocument(item)"
              >
                <span v-if="deletingId === item.id" class="spinner" />
                删除
              </button>
            </div>
          </div>
        </div>

        <p v-if="hasUnindexed" class="faint" style="margin-top: 12px">
          「已切块·未索引」表示文本已切好但还没写入向量库 ——
          这时面试检索不到它。点「重试索引」补齐。
        </p>
      </div>
    </template>
  </div>
</template>
