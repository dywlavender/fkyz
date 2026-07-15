import { Empty, Table, Typography } from 'antd'
import type { ColumnsType } from 'antd/es/table'

import type { RuleCheck } from '../types/review'
import { StatusBadge } from './StatusBadge'

interface RuleCheckTableProps {
  items?: RuleCheck[]
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

export function RuleCheckTable({ items = [] }: RuleCheckTableProps) {
  const sortedItems = [...items].sort((a, b) => verdictRank(a.verdict || a.conclusion) - verdictRank(b.verdict || b.conclusion))
  const ruleFilters = Array.from(new Set(items.map((item) => item.ruleId).filter(Boolean))).map((value) => ({ text: String(value), value: String(value) }))
  const columns: ColumnsType<RuleCheck> = [
    {
      title: '规则 ID',
      dataIndex: 'ruleId',
      width: 120,
      render: (value: RuleCheck['ruleId']) => value || '-',
      filters: ruleFilters,
      filterSearch: true,
      onFilter: (value, record) => String(record.ruleId || '').includes(String(value)),
    },
    {
      title: '结论',
      dataIndex: 'verdict',
      width: 120,
      render: (value: RuleCheck['verdict'], record) => <StatusBadge value={value || record.conclusion} />,
      filters: [
        { text: '不符合项', value: 'fail' },
        { text: '待确认项', value: 'confirm' },
        { text: '建议项', value: 'suggestion' },
        { text: '通过项', value: 'pass' },
      ],
      onFilter: (value, record) => String(record.verdict || record.conclusion || '').toLowerCase().includes(String(value)),
    },
    {
      title: '标准要求',
      dataIndex: 'standard',
      ellipsis: true,
      render: (value: RuleCheck['standard'], record) => (
        <Typography.Paragraph ellipsis={{ rows: 2, expandable: true, symbol: '展开' }} style={{ marginBottom: 0 }}>
          {value || record.rule || '-'}
        </Typography.Paragraph>
      ),
    },
    {
      title: '实现证据',
      dataIndex: 'actualEvidence',
      ellipsis: true,
      render: (value: RuleCheck['actualEvidence'], record: RuleCheck) => {
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
      title: '说明',
      dataIndex: 'reason',
      ellipsis: true,
      render: (value: RuleCheck['reason'], record) => (
        <Typography.Paragraph ellipsis={{ rows: 2, expandable: true, symbol: '展开' }} style={{ marginBottom: 0 }}>
          {value || record.risk || record.suggestion || '-'}
        </Typography.Paragraph>
      ),
    },
    {
      title: '严重级别',
      width: 110,
      render: (_value, record) => record.risk || record.suggestion ? <Typography.Text type="warning">medium</Typography.Text> : <Typography.Text type="secondary">-</Typography.Text>,
    },
  ]

  return (
    <Table
      rowKey={(record: RuleCheck, index?: number) => record.ruleId || record.id || String(index)}
      columns={columns}
      dataSource={sortedItems}
      size="small"
      pagination={items.length > 8 ? { pageSize: 8, size: 'small' } : false}
      locale={{ emptyText: <Empty description="暂无规则检查结果" /> }}
      scroll={{ x: 1120 }}
      expandable={{
        expandedRowRender: (record) => (
          <Typography.Text style={{ whiteSpace: 'pre-wrap' }}>
            {[
              `标准要求：${record.standard || record.rule || '-'}`,
              `实现证据：${textOf(record.actualEvidence || (record.evidence ? [record.evidence] : []))}`,
              `评审原因：${record.reason || record.risk || record.suggestion || '-'}`,
            ].join('\n\n')}
          </Typography.Text>
        ),
      }}
    />
  )
}
