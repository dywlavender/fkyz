import type { ApiDocItem, AppNotice, TaskLog } from '../../types/review'

export interface RequirementReviewPageProps {
  apiDocs: ApiDocItem[]
  onNotice: (notice: AppNotice) => void
}

export interface RequirementReviewFormValues {
  apiNames: string[]
  requirement: string
}

export interface RequirementExecutionLog {
  id: string
  time: string
  level: 'info' | 'success' | 'warning' | 'error'
  message: string
  detail?: string
  stream?: string
  isDebug?: boolean
}

export function taskLogTime(log: TaskLog) {
  return log.time ? new Date(log.time * 1000).toLocaleTimeString() : new Date().toLocaleTimeString()
}
