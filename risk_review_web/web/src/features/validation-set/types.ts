import type { CreateValidationCaseRequest } from '../../types/review'

export type CaseFormValues = CreateValidationCaseRequest & {
  apiDocIdsText?: string
  businessRuleIdsText?: string
  tagsText?: string
  expectedIssuePatternsText?: string
  forbiddenIssuePatternsText?: string
}
