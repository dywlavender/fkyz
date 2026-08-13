import type { TaskRecord, ValidationRun } from '../../types/review'

export const ALL_VALUE = '__all__'
export const REGRESSION_TASK_STORAGE_KEY = 'risk-review-validation-regression-task-id'
export const TERMINAL_TASK_STATUSES = new Set(['done', 'error', 'cancelled'])

export type RegressionDocumentType = 'review' | 'standard'

export function formatDate(value?: string) {
  if (!value) return '-'
  return new Date(value).toLocaleString('zh-CN', { hour12: false })
}

export function splitText(value?: string) {
  return String(value || '')
    .split(/[\n,，、]/)
    .map((item) => item.trim())
    .filter(Boolean)
}

export function joinValues(value?: string[]) {
  return (value || []).join('\n')
}

export function isTerminalTask(task?: TaskRecord) {
  return TERMINAL_TASK_STATUSES.has(String(task?.status || '').toLowerCase())
}

export function selectRegressionTask<T>(
  tasks: TaskRecord[],
  storedTask: TaskRecord<T> | undefined,
  clientId: string,
) {
  const regressions = tasks
    .filter((item) => item.taskType === 'regression')
    .sort((left, right) => (right.created || 0) - (left.created || 0)) as TaskRecord<T>[]
  const activeRegressions = regressions.filter((item) => !isTerminalTask(item))

  if (storedTask && !isTerminalTask(storedTask)) return storedTask
  return activeRegressions.find((item) => item.clientId === clientId)
    || activeRegressions[0]
    || storedTask
    || regressions.find((item) => item.clientId === clientId)
    || regressions[0]
}

export function taskStatusLabel(value?: string) {
  const labels: Record<string, string> = {
    queued: '排队中',
    running: '执行中',
    cancelling: '取消中',
    cancelled: '已取消',
    done: '已完成',
    error: '执行失败',
  }
  return labels[String(value || '').toLowerCase()] || value || '未知'
}

export function taskStatusColor(value?: string) {
  const status = String(value || '').toLowerCase()
  if (status === 'done') return 'success'
  if (status === 'error' || status === 'cancelled') return 'error'
  if (status === 'queued' || status === 'running' || status === 'cancelling') return 'processing'
  return 'default'
}

export function normalizeConclusion(value?: string) {
  const source = String(value || 'unreviewable').trim()
  const candidates: string[] = []
  if (/✅|\bpass\b|通过/.test(source) && !/不通过/.test(source)) candidates.push('pass')
  if (/\bfail\b|不通过|不符合/.test(source)) candidates.push('fail')
  if (/⛔|无法评审|\bunreviewable\b|\bunknown\b/.test(source)) candidates.push('unreviewable')
  if (candidates.length) return [...new Set(candidates)].join('|')
  const normalized = source.toLowerCase()
  if (normalized === 'unknown') return 'unreviewable'
  return normalized || 'unreviewable'
}

export function conclusionColor(value?: string) {
  const normalized = normalizeConclusion(value)
  if (normalized.includes('|')) return 'processing'
  if (normalized === 'pass') return 'success'
  if (normalized === 'fail') return 'error'
  if (normalized === 'unreviewable') return 'warning'
  return 'default'
}

export function conclusionLabel(value?: string) {
  const labels: Record<string, string> = {
    pass: '通过',
    fail: '不通过',
    unreviewable: '无法评审',
  }
  const normalized = normalizeConclusion(value)
  if (normalized.includes('|')) {
    return normalized.split('|').map((item) => labels[item] || item).join(' / ')
  }
  return labels[normalized] || normalized
}

export function conclusionIncludes(expected?: string, actual?: string) {
  const expectedValues = normalizeConclusion(expected)
    .split('|')
    .map((item) => item.trim())
    .filter(Boolean)
  return expectedValues.includes(normalizeConclusion(actual))
}

export function isTerminalRun(run?: ValidationRun) {
  return ['success', 'failed', 'partial_success'].includes(String(run?.status || ''))
}

export function runMatched(run?: ValidationRun) {
  if (!run || !isTerminalRun(run)) return undefined
  if (typeof run.matched === 'boolean') return run.matched
  if (run.status === 'success' && run.actualConclusion) {
    return conclusionIncludes(run.expectedConclusion, run.actualConclusion)
  }
  return run.matched
}

export function runAlertType(run?: ValidationRun): 'success' | 'info' | 'warning' | 'error' {
  if (!run || !isTerminalRun(run)) return 'info'
  if (run.status !== 'success') return 'error'
  return runMatched(run) ? 'success' : 'warning'
}

export function runDescription(run: ValidationRun) {
  if (run.error) return run.error
  const comparison = `期望 ${conclusionLabel(run.expectedConclusion)}，实际 ${conclusionLabel(run.actualConclusion)}`
  if (!isTerminalRun(run)) return `${comparison}，执行中，尚未形成最终一致性判断`
  return `${comparison}，${runMatched(run) ? '符合预期' : '不符合预期'}`
}
