import { Button, Card, DatePicker, Input, Modal, Select, Space, Table, Tag, Tooltip, Typography } from 'antd'
import type { ColumnsType } from 'antd/es/table'
import type { Key } from 'react'
import { useEffect, useMemo, useState } from 'react'

import { deleteReviewHistoryRecord } from '../api/reviewApi'
import { HeaderActions, usePageActive } from '../components/AppLayout'
import { EmptyState } from '../components/EmptyState'
import { ReviewHistoryDrawer, hasArtifacts, modeLabel, sourceLabel } from '../components/ReviewHistoryDrawer'
import { StatusBadge } from '../components/StatusBadge'
import type { AppNotice, ReviewHistoryRecord } from '../types/review'
import { formatDateTimeValue, mapVerdictToConclusion } from '../utils/reviewTransform'

interface HistoryProps {
  items: ReviewHistoryRecord[]
  selectedRecordId?: string
  onSelectedRecordConsumed?: () => void
  onReuse: (item: ReviewHistoryRecord) => void
  onNotice: (notice: AppNotice) => void
  onDeleted: (recordId: string) => void
}

function firstDownloadFile(record: ReviewHistoryRecord) {
  return record.reviewArtifacts?.md || record.standardArtifacts?.md || record.reviewArtifacts?.json || record.standardArtifacts?.json
}

function downloadHref(filename?: string) {
  return filename ? `/outputs/${encodeURIComponent(filename)}` : undefined
}

const DELETE_SUPPORTED = true
const ALL_VALUE = '__all__'

function artifactCompleteness(record: ReviewHistoryRecord) {
  const standardCount = record.standardArtifacts ? Object.values(record.standardArtifacts).filter(Boolean).length : 0
  const reviewCount = record.reviewArtifacts ? Object.values(record.reviewArtifacts).filter(Boolean).length : 0
  return { standardCount, reviewCount, total: standardCount + reviewCount }
}

function recordSearchText(record: ReviewHistoryRecord) {
  return [
    record.recordId,
    record.factorName,
    record.source,
    record.mode,
    record.conclusion,
    ...Object.values(record.standardArtifacts || {}),
    ...Object.values(record.reviewArtifacts || {}),
  ].filter(Boolean).join(' ').toLowerCase()
}

function createdTime(record: ReviewHistoryRecord) {
  const time = record.createdAt ? new Date(record.createdAt).getTime() : 0
  return Number.isNaN(time) ? 0 : time
}

export function History(props: HistoryProps) {
  const pageActive = usePageActive()
  const { items, selectedRecordId, onSelectedRecordConsumed, onReuse, onNotice, onDeleted } = props
  const [deleteTarget, setDeleteTarget] = useState<ReviewHistoryRecord | null>(null)
  const [batchDeleteOpen, setBatchDeleteOpen] = useState(false)
  const [selectedRowKeys, setSelectedRowKeys] = useState<Key[]>([])
  const [activeRecord, setActiveRecord] = useState<ReviewHistoryRecord | null>(null)
  const [deleting, setDeleting] = useState(false)
  const [query, setQuery] = useState('')
  const [conclusionFilter, setConclusionFilter] = useState(ALL_VALUE)
  const [dateRange, setDateRange] = useState<[string, string] | null>(null)

  useEffect(() => {
    if (!selectedRecordId) return
    const matched = items.find((item) => item.recordId === selectedRecordId)
    if (matched) setActiveRecord(matched)
    onSelectedRecordConsumed?.()
  }, [items, selectedRecordId, onSelectedRecordConsumed])

  async function confirmDelete() {
    if (!deleteTarget || !DELETE_SUPPORTED) return
    setDeleting(true)
    try {
      await deleteReviewHistoryRecord(deleteTarget.recordId)
      onDeleted(deleteTarget.recordId)
      setSelectedRowKeys((current) => current.filter((key) => key !== deleteTarget.recordId))
      if (activeRecord?.recordId === deleteTarget.recordId) setActiveRecord(null)
      setDeleteTarget(null)
      onNotice({ type: 'success', message: '历史记录已删除' })
    } catch (caught) {
      const message = caught instanceof Error ? caught.message : '删除失败'
      onNotice({ type: 'error', message })
    } finally {
      setDeleting(false)
    }
  }

  function confirmBatchDelete() {
    if (!DELETE_SUPPORTED) return
    if (!selectedRowKeys.length) return
    setBatchDeleteOpen(true)
  }

  async function executeBatchDelete() {
    const ids = selectedRowKeys.map(String)
    if (!ids.length || !DELETE_SUPPORTED) {
      setBatchDeleteOpen(false)
      return
    }
    setDeleting(true)
    const failed: Array<{ id: string; message: string }> = []
    const succeeded: string[] = []
    try {
      for (const id of ids) {
        try {
          await deleteReviewHistoryRecord(id)
          succeeded.push(id)
          onDeleted(id)
        } catch (caught) {
          failed.push({
            id,
            message: caught instanceof Error ? caught.message : '删除失败',
          })
        }
      }
      setSelectedRowKeys((current) => current.filter((key) => failed.some((item) => item.id === String(key))))
      if (activeRecord && succeeded.includes(activeRecord.recordId)) {
        setActiveRecord(null)
      }
      setBatchDeleteOpen(false)
      if (failed.length) {
        onNotice({
          type: 'warning',
          message: `批量删除完成：成功 ${ids.length - failed.length}，失败 ${failed.length}`,
          description: failed.slice(0, 3).map((item) => `${item.id}: ${item.message}`).join('；'),
        })
      } else {
        onNotice({ type: 'success', message: `已删除 ${ids.length} 条历史记录` })
      }
    } finally {
      setDeleting(false)
    }
  }

  const filteredItems = useMemo(() => {
    const lower = query.trim().toLowerCase()
    const startTime = dateRange?.[0] ? new Date(`${dateRange[0]} 00:00:00`).getTime() : 0
    const endTime = dateRange?.[1] ? new Date(`${dateRange[1]} 23:59:59`).getTime() : Number.POSITIVE_INFINITY
    return items.filter((record) => {
      if (lower && !recordSearchText(record).includes(lower)) return false
      if (conclusionFilter !== ALL_VALUE && mapVerdictToConclusion(record.conclusion) !== conclusionFilter) return false
      const time = createdTime(record)
      if (dateRange && (time < startTime || time > endTime)) return false
      return true
    })
  }, [items, query, conclusionFilter, dateRange])

  const columns: ColumnsType<ReviewHistoryRecord> = useMemo(() => [
    { title: '因子名称', dataIndex: 'factorName', ellipsis: true, render: (value: string) => value || '-' },
    { title: '来源', dataIndex: 'source', width: 100, render: (value: string) => <Tag>{sourceLabel(value)}</Tag> },
    { title: '执行模式', dataIndex: 'mode', width: 150, render: (value: string) => modeLabel(value) },
    { title: '最终结论', dataIndex: 'conclusion', width: 120, render: (value: string) => <StatusBadge value={value || 'confirm'} /> },
    { title: '风险数', dataIndex: 'riskCount', width: 100, render: (value: number) => value ?? 0 },
    {
      title: '文档状态',
      width: 190,
      render: (_: unknown, record: ReviewHistoryRecord) => {
        const completeness = artifactCompleteness(record)
        return (
          <Tooltip title={`准出产物 ${completeness.standardCount}/4，评审产物 ${completeness.reviewCount}/4，总计 ${completeness.total}/8。`}>
            <Space size={4} wrap>
              <Tag color={hasArtifacts(record.standardArtifacts) ? 'blue' : 'default'}>准出：{hasArtifacts(record.standardArtifacts) ? '有' : '无'}</Tag>
              <Tag color={hasArtifacts(record.reviewArtifacts) ? 'green' : 'default'}>评审：{hasArtifacts(record.reviewArtifacts) ? '有' : '无'}</Tag>
            </Space>
          </Tooltip>
        )
      },
    },
    { title: '创建时间', dataIndex: 'createdAt', width: 180, render: (value: string) => formatDateTimeValue(value) },
    {
      title: '操作',
      width: 280,
      fixed: 'right',
      render: (_: unknown, record: ReviewHistoryRecord) => {
        const downloadFile = firstDownloadFile(record)
        return (
          <Space onClick={(event) => event.stopPropagation()}>
            <Button type="link" onClick={() => setActiveRecord(record)}>查看</Button>
            <Button type="link" disabled={!record.standardArtifacts?.md} onClick={() => setActiveRecord(record)}>准出文档</Button>
            <Button type="link" disabled={!downloadFile} href={downloadHref(downloadFile)} target="_blank" download={downloadFile}>下载</Button>
            <Button type="link" disabled={!record.standardArtifacts?.json} onClick={() => onReuse(record)}>复用标准</Button>
            <Tooltip title={DELETE_SUPPORTED ? '后端会删除该记录关联产物；成功后才从列表移除。' : '当前后端不支持删除评审历史，不能执行前端假删除。'}>
              <Button danger type="link" disabled={!DELETE_SUPPORTED} onClick={() => setDeleteTarget(record)}>删除</Button>
            </Tooltip>
          </Space>
        )
      },
    },
  ], [onReuse])

  return (
    <Space className="app-page" direction="vertical" size={12} style={{ width: '100%' }}>
      <HeaderActions page="history">
        <Space className="app-header-action-space" size={8} wrap>
          <Button onClick={() => window.location.reload()}>刷新</Button>
          <Tooltip title={DELETE_SUPPORTED ? '按评审记录删除会调用后端接口删除关联产物，失败记录会保留。' : '当前后端不支持删除评审历史，不能执行前端假删除。'}>
            <Button danger loading={deleting} disabled={!DELETE_SUPPORTED || !selectedRowKeys.length} onClick={confirmBatchDelete}>
              删除所选
            </Button>
          </Tooltip>
        </Space>
      </HeaderActions>

      <Card className="compact-card" title="筛选评审记录">
        <Space className="responsive-filters" wrap size={8} style={{ width: '100%' }}>
          <Input.Search
            className="responsive-filter-search"
            allowClear
            placeholder="搜索因子名 / 记录 ID / 产物文件"
            value={query}
            onChange={(event) => setQuery(event.target.value)}
          />
          <Select
            className="responsive-filter-select"
            value={conclusionFilter}
            onChange={setConclusionFilter}
            options={[
              { label: '全部结论', value: ALL_VALUE },
              { label: '通过项', value: 'pass' },
              { label: '不符合项', value: 'fail' },
              { label: '待确认项', value: 'confirm' },
              { label: '建议项', value: 'suggestion' },
            ]}
          />
          <DatePicker.RangePicker
            className="responsive-filter-range"
            onChange={(_, values) => setDateRange(values[0] && values[1] ? [values[0], values[1]] : null)}
          />
          <Button
            onClick={() => {
              setQuery('')
              setConclusionFilter(ALL_VALUE)
              setDateRange(null)
            }}
          >
            重置
          </Button>
          <Typography.Text type="secondary">已选 {selectedRowKeys.length} 条，当前显示 {filteredItems.length} / {items.length} 条</Typography.Text>
        </Space>
      </Card>

      <Card className="compact-card" title="评审记录">
        {filteredItems.length ? (
          <Table
            rowKey="recordId"
            className="compact-table"
            columns={columns}
            dataSource={filteredItems}
            size="small"
            rowSelection={{
              selectedRowKeys,
              onChange: setSelectedRowKeys,
              renderCell: (_checked, record, _index, originNode) => (
                <span onClick={(event) => event.stopPropagation()}>{originNode}</span>
              ),
            }}
            onRow={(record) => ({
              onClick: () => setActiveRecord(record),
            })}
            pagination={{ pageSize: 10, size: 'small', showSizeChanger: true }}
            scroll={{ x: 1280 }}
          />
        ) : items.length ? (
          <EmptyState title="没有符合筛选条件的评审记录。" />
        ) : (
          <EmptyState title="暂无评审记录，先执行一次单因子评审。" />
        )}

        <ReviewHistoryDrawer
          record={activeRecord}
          open={pageActive && !!activeRecord}
          onClose={() => setActiveRecord(null)}
          onNotice={onNotice}
        />

        <Modal
          title="批量删除评审记录"
          open={pageActive && batchDeleteOpen}
          maskClosable={false}
          onCancel={() => setBatchDeleteOpen(false)}
          onOk={() => void executeBatchDelete()}
          confirmLoading={deleting}
          okText="批量删除"
          cancelText="取消"
          okButtonProps={{ danger: true, disabled: !DELETE_SUPPORTED || !selectedRowKeys.length }}
          cancelButtonProps={{ disabled: deleting }}
        >
          <Typography.Paragraph>
            将删除已选 {selectedRowKeys.length} 条记录及其关联准出文档和评审结果文档，请确认。
          </Typography.Paragraph>
          <Typography.Text type="secondary">
            删除失败的记录会继续保留在列表和选中项中。
          </Typography.Text>
        </Modal>

        <Modal
          title="删除评审记录"
          open={pageActive && !!deleteTarget}
          maskClosable={false}
          onCancel={() => setDeleteTarget(null)}
          onOk={confirmDelete}
          confirmLoading={deleting}
          okText="删除"
          okButtonProps={{ danger: true, disabled: !DELETE_SUPPORTED }}
        >
          <Typography.Paragraph>
            将调用后端删除该记录关联的准出文档和评审结果文档，包括 Markdown、JSON、raw、validation 文件。只有后端删除成功后，列表才会移除该记录。
          </Typography.Paragraph>
          <Typography.Text strong>{deleteTarget?.factorName || deleteTarget?.recordId}</Typography.Text>
        </Modal>
      </Card>
    </Space>
  )
}
