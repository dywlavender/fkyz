import { Empty, Space, Table, Tag, Typography } from 'antd'
import type { ColumnsType } from 'antd/es/table'

import type { StepCheck } from '../types/review'
import { StatusBadge } from './StatusBadge'

interface StepCheckTableProps {
  items?: StepCheck[]
  onStepClick?: (stepNo?: number) => void
}

function verdictRank(value?: string) {
  const normalized = String(value || '').toLowerCase()
  if (normalized === 'fail') return 0
  if (normalized === 'confirm') return 1
  if (normalized === 'pass') return 2
  return 3
}

function textOf(value?: string | string[]) {
  return Array.isArray(value) ? value.join('\n') : value || '-'
}

export function StepCheckTable({ items = [], onStepClick }: StepCheckTableProps) {
  const sortedItems = [...items].sort((a, b) => verdictRank(a.verdict || a.conclusion) - verdictRank(b.verdict || b.conclusion))
  const stepTypeFilters = Array.from(new Set(items.map((item) => item.stepType).filter(Boolean))).map((value) => ({ text: String(value), value: String(value) }))
  const columns: ColumnsType<StepCheck> = [
    {
      title: '步骤号',
      width: 100,
      render: (_: unknown, record: StepCheck) => record.stepNo ? (
        <Typography.Link onClick={() => onStepClick?.(record.stepNo)}>Step {record.stepNo}</Typography.Link>
      ) : '-',
    },
    {
      title: '类型',
      dataIndex: 'stepType',
      width: 120,
      render: (value: StepCheck['stepType']) => value || '-',
      filters: stepTypeFilters,
      onFilter: (value, record) => String(record.stepType || '') === String(value),
    },
    {
      title: '结论',
      dataIndex: 'verdict',
      width: 120,
      render: (value: StepCheck['verdict'], record) => <StatusBadge value={value || record.conclusion} />,
      filters: [
        { text: '不符合项', value: 'fail' },
        { text: '待确认项', value: 'confirm' },
        { text: '建议项', value: 'suggestion' },
        { text: '通过项', value: 'pass' },
      ],
      onFilter: (value, record) => String(record.verdict || record.conclusion || '').toLowerCase().includes(String(value)),
    },
    {
      title: '标准步骤',
      dataIndex: 'standardStep',
      ellipsis: true,
      render: (value: StepCheck['standardStep'], record) => (
        <Typography.Paragraph ellipsis={{ rows: 2, expandable: true, symbol: '展开' }} style={{ marginBottom: 0 }}>
          {value || record.step || '-'}
        </Typography.Paragraph>
      ),
    },
    {
      title: '实现证据',
      dataIndex: 'actualEvidence',
      ellipsis: true,
      render: (value: StepCheck['actualEvidence'], record: StepCheck) => {
        const lines = value?.length ? value : record.evidence ? [record.evidence] : []
        return lines.length ? (
          <ul style={{ margin: 0, paddingLeft: 18, maxHeight: 88, overflow: 'auto' }}>
            {lines.map((line: string) => (
              <li key={line}>{line}</li>
            ))}
          </ul>
        ) : '-'
      },
    },
    {
      title: '评审原因',
      dataIndex: 'reason',
      ellipsis: true,
      render: (value: StepCheck['reason'], record) => (
        <Typography.Paragraph ellipsis={{ rows: 2, expandable: true, symbol: '展开' }} style={{ marginBottom: 0 }}>
          {value || record.risk || record.suggestion || '-'}
        </Typography.Paragraph>
      ),
    },
    {
      title: '关联规则',
      dataIndex: 'relatedRuleIds',
      width: 180,
      render: (value: StepCheck['relatedRuleIds']) => value?.length ? (
        <Space size={[4, 4]} wrap>{value.map((id) => <Tag key={id}>{id}</Tag>)}</Space>
      ) : <Typography.Text type="secondary">未关联规则</Typography.Text>,
    },
  ]

  return (
    <Table
      rowKey={(record: StepCheck, index?: number) => `${record.stepNo || 'step'}-${index}`}
      columns={columns}
      dataSource={sortedItems}
      size="small"
      pagination={items.length > 8 ? { pageSize: 8, size: 'small' } : false}
      locale={{ emptyText: <Empty description="暂无加工步骤检查结果" /> }}
      scroll={{ x: 1240 }}
      expandable={{
        expandedRowRender: (record) => (
          <Typography.Text style={{ whiteSpace: 'pre-wrap' }}>
            {[
              `标准步骤：${record.standardStep || record.step || '-'}`,
              `实现证据：${textOf(record.actualEvidence || (record.evidence ? [record.evidence] : []))}`,
              `评审原因：${record.reason || record.risk || record.suggestion || '-'}`,
              `关联规则：${record.relatedRuleIds?.join('、') || '未关联规则'}`,
            ].join('\n\n')}
          </Typography.Text>
        ),
      }}
    />
  )
}
