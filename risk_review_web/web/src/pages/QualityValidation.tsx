import { EyeOutlined, ReloadOutlined } from '@ant-design/icons'
import { Button, Drawer, Progress, Space, Table, Tag, Typography } from 'antd'
import type { ColumnsType } from 'antd/es/table'
import { useCallback, useEffect, useState } from 'react'

import { fetchValidationRunGroups } from '../api/reviewApi'
import { EmptyState } from '../components/EmptyState'
import { HeaderActions, usePageActive } from '../components/AppLayout'
import { StatusBadge } from '../components/StatusBadge'
import { TaskStatusBadge } from '../components/TaskStatusBadge'
import type { AppNotice, ExampleItem, ValidationRun } from '../types/review'
import { formatDateTimeValue } from '../utils/reviewTransform'
import { ValidationSet } from './ValidationSet'

interface QualityValidationProps {
  view: 'runs' | 'cases'
  examples: ExampleItem[]
  onNotice: (notice: AppNotice) => void
}

interface ValidationBatch {
  groupId: string
  runs: ValidationRun[]
  startedAt?: string
  finishedAt?: string
  total: number
  matched: number
  mismatched: number
  failed: number
  status: string
  agreementRate: number
}

function ValidationRunsView({ onNotice }: { onNotice: (notice: AppNotice) => void }) {
  const pageActive = usePageActive()
  const [batches, setBatches] = useState<ValidationBatch[]>([])
  const [page, setPage] = useState(1)
  const [pageSize, setPageSize] = useState(20)
  const [total, setTotal] = useState(0)
  const [loading, setLoading] = useState(false)
  const [activeBatch, setActiveBatch] = useState<ValidationBatch | null>(null)

  const load = useCallback(async () => {
    setLoading(true)
    try {
      const result = await fetchValidationRunGroups(page, pageSize)
      setBatches(result.items)
      setTotal(result.total)
    } catch (caught) {
      onNotice({ type: 'error', message: caught instanceof Error ? caught.message : '验证执行批次加载失败' })
    } finally {
      setLoading(false)
    }
  }, [onNotice, page, pageSize])

  useEffect(() => { void load() }, [load])

  const columns: ColumnsType<ValidationBatch> = [
    {
      title: '执行批次',
      dataIndex: 'groupId',
      ellipsis: true,
      render: (value: string) => <Typography.Text copyable={{ text: value }}>{value.slice(0, 16)}</Typography.Text>,
    },
    { title: '任务状态', dataIndex: 'status', width: 110, render: (value: string) => <TaskStatusBadge value={value} /> },
    { title: '案例总数', dataIndex: 'total', width: 100 },
    { title: '符合预期', dataIndex: 'matched', width: 100, render: (value: number) => <Tag color="success">{value}</Tag> },
    { title: '不符合预期', dataIndex: 'mismatched', width: 110, render: (value: number) => <Tag color={value ? 'error' : 'default'}>{value}</Tag> },
    { title: '执行失败', dataIndex: 'failed', width: 100, render: (value: number) => <Tag color={value ? 'error' : 'default'}>{value}</Tag> },
    {
      title: '期望一致率',
      dataIndex: 'agreementRate',
      width: 160,
      render: (value: number) => <Progress percent={value} size="small" />,
    },
    { title: '执行时间', dataIndex: 'startedAt', width: 180, render: (value?: string) => formatDateTimeValue(value) },
    {
      title: '操作',
      fixed: 'right',
      width: 90,
      render: (_value, record) => <Button type="link" icon={<EyeOutlined />} onClick={() => setActiveBatch(record)}>查看</Button>,
    },
  ]

  const runColumns: ColumnsType<ValidationRun> = [
    { title: '案例名称', dataIndex: 'caseName', ellipsis: true },
    { title: '执行状态', dataIndex: 'status', width: 110, render: (value: string) => <TaskStatusBadge value={value === 'success' ? 'done' : value === 'failed' ? 'error' : value} /> },
    { title: '期望结论', dataIndex: 'expectedConclusion', width: 110, render: (value?: string) => <StatusBadge value={value || 'unreviewable'} /> },
    { title: '实际结论', dataIndex: 'actualConclusion', width: 110, render: (value?: string) => value ? <StatusBadge value={value} /> : '-' },
    { title: '是否一致', dataIndex: 'matched', width: 100, render: (value?: boolean) => value == null ? '-' : <Tag color={value ? 'success' : 'error'}>{value ? '一致' : '不一致'}</Tag> },
    { title: '错误原因', dataIndex: 'error', ellipsis: true, render: (value?: string) => value || '-' },
  ]

  return (
    <Space direction="vertical" size={12} style={{ width: '100%' }}>
      <HeaderActions page="validation-set">
        <Button icon={<ReloadOutlined />} loading={loading} onClick={() => void load()}>刷新</Button>
      </HeaderActions>
      {batches.length || loading ? (
        <Table
          rowKey="groupId"
          className="compact-table"
          loading={loading}
          columns={columns}
          dataSource={batches}
          onRow={(record) => ({ onClick: () => setActiveBatch(record) })}
          pagination={{
            current: page,
            pageSize,
            total,
            showSizeChanger: true,
            showTotal: (value) => `共 ${value} 个批次`,
            onChange: (nextPage, nextPageSize) => {
              setPage(nextPage)
              setPageSize(nextPageSize)
            },
          }}
          scroll={{ x: 1100 }}
        />
      ) : (
        <EmptyState title="暂无验证执行批次，请在“验证案例”中发起执行。" />
      )}
      <Drawer
        title="验证批次详情"
        width="min(1120px, 86vw)"
        open={pageActive && !!activeBatch}
        onClose={() => setActiveBatch(null)}
      >
        {activeBatch ? (
          <Table
            rowKey="runId"
            className="compact-table"
            columns={runColumns}
            dataSource={activeBatch.runs}
            pagination={false}
            scroll={{ x: 900 }}
          />
        ) : null}
      </Drawer>
    </Space>
  )
}

export function QualityValidation(props: QualityValidationProps) {
  return (
    <div className="quality-validation-page">
      <div style={{ display: props.view === 'runs' ? 'block' : 'none' }}>
        <ValidationRunsView onNotice={props.onNotice} />
      </div>
      <div style={{ display: props.view === 'cases' ? 'block' : 'none' }}>
        <ValidationSet
          headerPage="validation-cases"
          examples={props.examples}
          onNotice={props.onNotice}
        />
      </div>
    </div>
  )
}
