import type { ApiDocItem, AppNotice, ExampleItem, ReviewHistoryRecord, ValidationCase } from '../../types/review'

export interface SingleReviewProps {
  apiDocs: ApiDocItem[]
  examples: ExampleItem[]
  onNotice: (notice: AppNotice) => void
  prefillStandardRecord?: ReviewHistoryRecord | null
  onPrefillStandardConsumed?: () => void
}

export interface ReviewFormValues {
  factorName: string
  requirement: string
  apiNames: string[]
  businessRuleIds?: string[]
  paramFields?: string
  procLogic?: string
  defaultValues?: string
}

export type CaseOption = ExampleItem | ValidationCase

export interface ExecutionLogItem {
  id: string
  time: string
  phase: 'phase1' | 'phase2' | 'system'
  level: 'info' | 'success' | 'warning' | 'error'
  message: string
  detail?: string
  stream?: string
  isDebug?: boolean
}
