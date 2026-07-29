import { Typography } from 'antd'
import type { ColumnsType } from 'antd/es/table'

import { StatusBadge } from '../../components/StatusBadge'
import type { ExampleItem, ValidationCase } from '../../types/review'
import { formatDateTimeValue } from '../../utils/reviewTransform'
import type { CaseOption } from './types'

export function caseDisplayName(item: CaseOption) {
  return 'caseName' in item ? item.caseName : item.title || item.id
}

export function caseId(item: CaseOption) {
  return 'caseId' in item ? item.caseId : item.id
}

export function caseKey(item: CaseOption) {
  return 'caseId' in item ? `validation:${item.caseId}` : `example:${item.id}`
}

export function caseFactorName(item: CaseOption) {
  return item.factorName || '-'
}

export function caseExpectedConclusion(item: CaseOption) {
  return 'caseId' in item ? item.expectedConclusion : item.expected
}

export function caseReviewContent(item: CaseOption) {
  return 'caseId' in item ? item.reviewContent || '' : item.reviewContent || item.procLogic || item.draft || ''
}

export function caseApiNames(item: CaseOption) {
  return 'caseId' in item ? item.apiDocIds || [] : item.apiNames || []
}

export function buildCaseColumns(): ColumnsType<CaseOption> {
  return [
    { title: '案例名称', width: 220, render: (_: unknown, record) => caseDisplayName(record) },
    { title: '因子名称', width: 180, render: (_: unknown, record) => caseFactorName(record) },
    { title: '期望结论', width: 110, render: (_: unknown, record) => caseExpectedConclusion(record) || '-' },
    {
      title: '最近执行结果',
      width: 130,
      render: (_: unknown, record) => (
        'lastActualConclusion' in record && record.lastActualConclusion ? <StatusBadge value={record.lastActualConclusion} /> : <Typography.Text type="secondary">-</Typography.Text>
      ),
    },
    { title: '更新时间', width: 180, render: (_: unknown, record) => formatDateTimeValue(record.updatedAt) },
  ]
}

export function isValidationCase(item: ExampleItem | ValidationCase): item is ValidationCase {
  return 'caseId' in item
}
