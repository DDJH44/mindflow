/**
 * 「按聚焦方向再面一场」—— 报告页与画像页共用的开始逻辑。
 *
 * 首页（HomePage）有一份带额度刷新的版本，刻意**不**并入这里：
 * 它与首页的 `load()` 耦合（额度用尽时要刷新整页额度显示），
 * 硬抽会把两个页面的加载逻辑也搅进来。此处只收纯"开一场"
 * 的三步：create（草稿，不扣额度）→ start（生成首题，扣一场）
 * → 跳转面试页。
 *
 * 失败处理：create/start 的错误原样展示（含额度不足的后端文案），
 * 不自动重试 —— 用户能看懂"额度用完"并自己决定。
 */
import { ref } from 'vue'
import { useRouter } from 'vue-router'

import { interviewApi } from '../api'
import { ApiError } from '../api/http'

export function useFocusedInterview() {
  const router = useRouter()

  const starting = ref(false)
  const startError = ref('')

  async function startFocused(options: {
    projectId: number
    /** 继承上一场的岗位（可为 null，后端退化为面试类型）。 */
    targetRole?: string | null
    /** 继承上一场的题目上限；缺省用首页同一个默认值。 */
    maxQuestions?: number
    /** 服务端给的检索方向 —— 这场面试的"聚焦"所在。 */
    query: string | null
  }) {
    if (starting.value || !options.query) {
      return
    }

    starting.value = true
    startError.value = ''

    try {
      const session = await interviewApi.create({
        project_id: options.projectId,
        target_role: options.targetRole ?? null,
        max_questions: options.maxQuestions ?? 8,
      })

      await interviewApi.start(session.id, options.query)

      await router.push({
        name: 'interview',
        params: { id: session.id },
      })
    } catch (err) {
      startError.value =
        err instanceof ApiError ? err.message : '开始面试失败'
    } finally {
      starting.value = false
    }
  }

  return { starting, startError, startFocused }
}
