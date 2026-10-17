/**
 * 会话状态的中文标签（**单一真相**）。
 *
 * ## 为什么抽成共享模块
 *
 * 这份映射原本在 `HistoryPage.vue` 与 `ProjectPage.vue` 里
 * **各写了一份**，而 `InterviewPage.vue` 忘了做 ——
 * 于是面试页的标签直接渲染了后端英文值：
 *
 *     第 1 / 8 题   [paused]
 *
 * 这是"同一个映射被抄到多处"的典型后果：
 * 抄的时候不会错，**漏掉一处才会错** —— 而漏掉不会报错，
 * 它只会让界面上出现一个英文单词。实测就是这么发现的
 * （靠截图，不是靠类型检查）。
 *
 * ## 为什么未命中时回退原值
 *
 * 后端新增状态时，与其显示空白，不如显示原始值 ——
 * 一个英文单词至少能让人搜到它，空白则会让人以为界面坏了。
 *
 * 但**回退不等于合格**：新增状态时应当同步补这里。
 * `STATUS_LABEL` 的键必须覆盖后端 `SessionStatus` 的全部取值。
 */

export const STATUS_LABEL: Record<string, string> = {
  created: '未完成',
  draft: '未开始',
  preparing_context: '准备中',
  planned: '已计划',
  asking: '待作答',
  waiting_for_answer: '待作答',
  evaluating: '评价中',
  summarizing: '生成总结',
  completed: '已完成',
  paused: '已暂停',
  cancelled: '已取消',
  failed: '失败',
}

export function statusLabel(status: string | null | undefined): string {
  if (!status) {
    return ''
  }

  return STATUS_LABEL[status] ?? status
}
