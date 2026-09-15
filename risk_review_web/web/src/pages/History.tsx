import { DeleteOutlined, DownloadOutlined, FileExcelOutlined, InboxOutlined, MoreOutlined, ReloadOutlined } from '@ant-design/icons'
import { Button, DatePicker, Dropdown, Input, Modal, Select, Space, Table, Tag, Typography } from 'antd'
import type { ColumnsType } from 'antd/es/table'
import type { Dayjs } from 'dayjs'
import { useCallback, useEffect, useMemo, useState } from 'react'

import { deleteReviewHistoryRecord, fetchReviewRecord, fetchReviewRecords, setReviewRecordArchived } from '../api/reviewApi'
import { HeaderActions, usePageActive } from '../components/AppLayout'
import { ReviewHistoryDrawer, sourceLabel } from '../components/ReviewHistoryDrawer'
import { StatusBadge } from '../components/StatusBadge'
import { TaskStatusBadge } from '../components/TaskStatusBadge'
import type { AppNotice, ReviewHistoryRecord } from '../types/review'
import { formatDateTimeValue } from '../utils/reviewTransform'

interface HistoryProps {
  selectedRecordId?: string
  onSelectedRecordConsumed?: () => void
  onReuse: (item: ReviewHistoryRecord) => void
  onNotice: (notice: AppNotice) => void
}

const ALL_VALUE = '__all__'

function firstDownloadFile(record: ReviewHistoryRecord) {
  return record.reviewArtifacts?.md || record.standardArtifacts?.md
}

function downloadHref(filename?: string) {
  return filename ? `/outputs/${encodeURIComponent(filename)}` : undefined
}

export function History(props: HistoryProps) {
  const pageActive = usePageActive()
  const { selectedRecordId, onSelectedRecordConsumed, onReuse, onNotice } = props
  const [records, setRecords] = useState<ReviewHistoryRecord[]>([])
  const [total, setTotal] = useState(0)
  const [page, setPage] = useState(1)
  const [pageSize, setPageSize] = useState(20)
  const [keyword, setKeyword] = useState('')
  const [source, setSource] = useState(ALL_VALUE)
  const [taskStatus, setTaskStatus] = useState(ALL_VALUE)
  const [conclusion, setConclusion] = useState(ALL_VALUE)
  const [dateRange, setDateRange] = useState<[Dayjs | null, Dayjs | null] | null>(null)
  const [showArchived, setShowArchived] = useState(false)
  const [loading, setLoading] = useState(false)
  const [activeRecord, setActiveRecord] = useState<ReviewHistoryRecord | null>(null)
  const [archiveTarget, setArchiveTarget] = useState<ReviewHistoryRecord | null>(null)
  const [selectedRecordIds, setSelectedRecordIds] = useState<string[]>([])
  const [deleteRecordIds, setDeleteRecordIds] = useState<string[]>([])
  const [deleting, setDeleting] = useState(false)

  async function openRecord(record: ReviewHistoryRecord) {
    try {
      const detail = await fetchReviewRecord(record.recordId)
      setActiveRecord(detail)
    } catch (caught) {
      onNotice({ type: 'error', message: caught instanceof Error ? caught.message : '评审详情加载失败' })
    }
  }

  const loadRecords = useCallback(async (silent = false) => {
    if (!silent) setLoading(true)
    try {
      const result = await fetchReviewRecords({
        page,
        pageSize,
        keyword: keyword.trim(),
        source: source === ALL_VALUE ? undefined : source,
        taskStatus: taskStatus === ALL_VALUE ? undefined : taskStatus,
        conclusion: conclusion === ALL_VALUE ? undefined : conclusion,
        archived: showArchived,
        dateFrom: dateRange?.[0]?.startOf('day').toISOString(),
        dateTo: dateRange?.[1]?.endOf('day').toISOString(),
      })
      setRecords(result.items || [])
      setTotal(result.total || 0)
    } catch (caught) {
      if (!silent) {
        onNotice({ type: 'error', message: caught instanceof Error ? caught.message : '评审记录加载失败' })
      }
    } finally {
      if (!silent) setLoading(false)
    }
  }, [conclusion, dateRange, keyword, onNotice, page, pageSize, showArchived, source, taskStatus])

  useEffect(() => {
    const timer = window.setTimeout(() => void loadRecords(), 180)
    return () => window.clearTimeout(timer)
  }, [loadRecords])

  useEffect(() => {
    if (!pageActive) return
    const timer = window.setInterval(() => void loadRecords(true), 3000)
    return () => window.clearInterval(timer)
  }, [loadRecords, pageActive])

  useEffect(() => {
    if (!selectedRecordId) return
    const matched = records.find((item) => item.recordId === selectedRecordId)
    if (matched) void openRecord(matched)
    onSelectedRecordConsumed?.()
  }, [records, selectedRecordId, onSelectedRecordConsumed])

  async function confirmArchive() {
    if (!archiveTarget) return
    try {
      await setReviewRecordArchived(archiveTarget.recordId, !archiveTarget.archived)
      onNotice({ type: 'success', message: archiveTarget.archived ? '记录已恢复' : '记录已归档' })
      setArchiveTarget(null)
      setActiveRecord(null)
      await loadRecords()
    } catch (caught) {
      onNotice({ type: 'error', message: caught instanceof Error ? caught.message : '归档操作失败' })
    }
  }

  async function confirmDelete() {
    if (!deleteRecordIds.length) return
    setDeleting(true)
    const results = await Promise.allSettled(
      deleteRecordIds.map((recordId) => {
        const record = records.find((item) => item.recordId === recordId)
        return deleteReviewHistoryRecord(recordId, record?.taskId)
      })
    )
    const failed = results.filter((result) => result.status === 'rejected')
    const succeededIds = deleteRecordIds.filter((_, index) => results[index]?.status === 'fulfilled')
    setSelectedRecordIds((current) => current.filter((recordId) => !succeededIds.includes(recordId)))
    setDeleteRecordIds([])
    setActiveRecord(null)
    setDeleting(false)
    await loadRecords()
    if (failed.length) {
      const firstError = failed[0]
      const reason = firstError.status === 'rejected' && firstError.reason instanceof Error
        ? firstError.reason.message
        : '删除失败'
      onNotice({
        type: 'error',
        message: `删除完成：成功 ${succeededIds.length}，失败 ${failed.length}。${reason}`,
      })
      return
    }
    onNotice({ type: 'success', message: `已删除 ${succeededIds.length} 条评审记录` })
  }

  async function exportRecords() {
    try {
      const result = await fetchReviewRecords({
        page: 1,
        pageSize: 5000,
        keyword: keyword.trim(),
        source: source === ALL_VALUE ? undefined : source,
        taskStatus: taskStatus === ALL_VALUE ? undefined : taskStatus,
        conclusion: conclusion === ALL_VALUE ? undefined : conclusion,
        archived: showArchived,
        dateFrom: dateRange?.[0]?.startOf('day').toISOString(),
        dateTo: dateRange?.[1]?.endOf('day').toISOString(),
        export: true,
      })
      const safeCell = (value: unknown) => {
        const raw = String(value ?? '')
        const protectedValue = /^[=+\-@]/.test(raw) ? `'${raw}` : raw
        return `"${protectedValue.replace(/"/g, '""')}"`
      }
      const rows = [
        ['任务名称', '任务编号', '来源', '任务状态', '评审结论', '问题数', '创建时间', '更新时间'],
        ...result.items.map((record) => [
          record.title || record.factorName || '',
          record.taskId || record.recordId,
          sourceLabel(record.source || ''),
          record.taskStatus || '',
          record.conclusion || '',
          record.riskCount || 0,
          record.createdAt || '',
          record.updatedAt || '',
        ]),
      ]
      const csv = `\uFEFF${rows.map((row) => row.map(safeCell).join(',')).join('\n')}`
      const url = URL.createObjectURL(new Blob([csv], { type: 'text/csv;charset=utf-8' }))
      const anchor = document.createElement('a')
      anchor.href = url
      anchor.download = `评审记录-${new Date().toISOString().slice(0, 10)}.csv`
      anchor.click()
      URL.revokeObjectURL(url)
    } catch (caught) {
      onNotice({ type: 'error', message: caught instanceof Error ? caught.message : '导出评审记录失败' })
    }
  }

  const columns: ColumnsType<ReviewHistoryRecord> = useMemo(() => [
    {
      title: '任务名称',
      dataIndex: 'title',
      ellipsis: true,
      render: (value: string, record) => (
        <Space direction="vertical" size={0}>
          <Typography.Text strong>{value || record.factorName || '-'}</Typography.Text>
          <Typography.Text type="secondary">{record.taskId?.slice(0, 8) || record.recordId}</Typography.Text>
        </Space>
      ),
    },
    { title: '来源', dataIndex: 'source', width: 110, render: (value: string) => <Tag>{sourceLabel(value)}</Tag> },
    { title: '任务状态', dataIndex: 'taskStatus', width: 110, render: (value: string) => <TaskStatusBadge value={value || 'done'} /> },
    {
      title: '评审结论',
      dataIndex: 'conclusion',
      width: 110,
      render: (value: string, record) => (
        ['queued', 'running', 'cancelling'].includes(String(record.taskStatus || ''))
          ? <Typography.Text type="secondary">待完成</Typography.Text>
          : <StatusBadge value={value || 'unreviewable'} />
      ),
    },
    {
      title: '结果汇总',
      width: 150,
      render: (_: unknown, record) => record.summary?.total
        ? `${record.summary.completed || 0}/${record.summary.total} 完成`
        : `${record.riskCount || 0} 个问题`,
    },
    { title: '更新时间', dataIndex: 'updatedAt', width: 180, render: (value: string, record) => formatDateTimeValue(value || record.createdAt) },
    {
      title: '操作',
      fixed: 'right',
      width: 130,
      render: (_: unknown, record) => {
        const downloadFile = firstDownloadFile(record)
        return (
          <Space onClick={(event) => event.stopPropagation()}>
            <Button type="link" onClick={() => void openRecord(record)}>查看</Button>
            <Dropdown
              menu={{
                items: [
                  {
                    key: 'reuse',
                    label: '重新评审',
                    disabled: !record.standardArtifacts?.json,
                    onClick: () => onReuse(record),
                  },
                  {
                    key: 'download',
                    label: (
                      <a href={downloadHref(downloadFile)} download={downloadFile}>
                        <DownloadOutlined /> 下载报告
                      </a>
                    ),
                    disabled: !downloadFile,
                  },
                  {
                    key: 'archive',
                    label: <><InboxOutlined /> {record.archived ? '恢复' : '归档'}</>,
                    danger: !record.archived,
                    disabled: ['queued', 'running', 'cancelling'].includes(String(record.taskStatus || '')),
                    onClick: () => setArchiveTarget(record),
                  },
                  {
                    key: 'delete',
                    label: <><DeleteOutlined /> 删除</>,
                    danger: true,
                    disabled: ['queued', 'running', 'cancelling'].includes(String(record.taskStatus || '')),
                    onClick: () => setDeleteRecordIds([record.recordId]),
                  },
                ],
              }}
            >
              <Button type="text" icon={<MoreOutlined />} aria-label="更多操作" />
            </Dropdown>
          </Space>
        )
      },
    },
  ], [onReuse])

  return (
    <Space className="app-page records-page" direction="vertical" size={12} style={{ width: '100%' }}>
      <HeaderActions page="history">
        <Button icon={<ReloadOutlined />} loading={loading} onClick={() => void loadRecords()}>刷新</Button>
      </HeaderActions>

      <div className="record-toolbar">
        <Input.Search
          allowClear
          placeholder="搜索任务名称、因子名称或任务编号"
          value={keyword}
          onChange={(event) => { setKeyword(event.target.value); setPage(1) }}
          style={{ width: 320 }}
        />
        <Select
          value={source}
          onChange={(value) => { setSource(value); setPage(1) }}
          style={{ width: 140 }}
          options={[
            { label: '全部来源', value: ALL_VALUE },
            { label: '单因子', value: 'single' },
            { label: '批量', value: 'batch' },
            { label: '定时', value: 'scheduled' },
            { label: '质量验证', value: 'validation' },
            { label: '模式对比', value: 'experiment' },
          ]}
        />
        <Select
          value={taskStatus}
          onChange={(value) => { setTaskStatus(value); setPage(1) }}
          style={{ width: 140 }}
          options={[
            { label: '全部任务状态', value: ALL_VALUE },
            { label: '排队中', value: 'queued' },
            { label: '执行中', value: 'running' },
            { label: '终止中', value: 'cancelling' },
            { label: '已完成', value: 'done' },
            { label: '执行失败', value: 'error' },
            { label: '已取消', value: 'cancelled' },
          ]}
        />
        <Select
          value={conclusion}
          onChange={(value) => { setConclusion(value); setPage(1) }}
          style={{ width: 140 }}
          options={[
            { label: '全部评审结论', value: ALL_VALUE },
            { label: '通过', value: 'pass' },
            { label: '不通过', value: 'fail' },
            { label: '无法评审', value: 'unreviewable' },
          ]}
        />
        <DatePicker.RangePicker
          value={dateRange}
          onChange={(value) => {
            setDateRange(value as [Dayjs | null, Dayjs | null] | null)
            setPage(1)
          }}
          allowClear
        />
        <Button type={showArchived ? 'primary' : 'default'} onClick={() => { setShowArchived((value) => !value); setPage(1) }}>
          {showArchived ? '查看有效记录' : '查看归档'}
        </Button>
        <Button
          danger
          icon={<DeleteOutlined />}
          disabled={!selectedRecordIds.length}
          onClick={() => setDeleteRecordIds(selectedRecordIds)}
        >
          删除所选
        </Button>
        <Button icon={<FileExcelOutlined />} onClick={() => void exportRecords()}>导出 CSV</Button>
      </div>

      <Table
        rowKey="recordId"
        className="compact-table"
        loading={loading}
        columns={columns}
        dataSource={records}
        rowSelection={{
          selectedRowKeys: selectedRecordIds,
          onChange: (keys) => setSelectedRecordIds(keys.map(String)),
          getCheckboxProps: (record) => ({
            disabled: ['queued', 'running', 'cancelling'].includes(String(record.taskStatus || '')),
          }),
        }}
        onRow={(record) => ({ onClick: () => void openRecord(record) })}
        pagination={{
          current: page,
          pageSize,
          total,
          showSizeChanger: true,
          showTotal: (value) => `共 ${value} 条`,
          onChange: (nextPage, nextSize) => {
            setPage(nextPage)
            setPageSize(nextSize)
          },
        }}
        scroll={{ x: 980 }}
      />

      <ReviewHistoryDrawer
        record={activeRecord}
        open={pageActive && !!activeRecord}
        onClose={() => setActiveRecord(null)}
        onNotice={onNotice}
      />

      <Modal
        title={archiveTarget?.archived ? '恢复评审记录？' : '归档评审记录？'}
        open={pageActive && !!archiveTarget}
        onCancel={() => setArchiveTarget(null)}
        onOk={() => void confirmArchive()}
        okText={archiveTarget?.archived ? '恢复' : '归档'}
        okButtonProps={{ danger: !archiveTarget?.archived }}
      >
        <Typography.Paragraph>
          {archiveTarget?.archived
            ? '恢复后记录会重新出现在默认列表中。'
            : '归档后默认列表不再显示，但标准规范和评审报告不会被删除，可以随时恢复。'}
        </Typography.Paragraph>
      </Modal>

      <Modal
        title={`删除 ${deleteRecordIds.length} 条评审记录？`}
        open={pageActive && deleteRecordIds.length > 0}
        onCancel={() => setDeleteRecordIds([])}
        onOk={() => void confirmDelete()}
        okText="删除"
        confirmLoading={deleting}
        okButtonProps={{ danger: true }}
      >
        <Typography.Paragraph>
          删除后，记录关联的标准规范、结构化摘要和评审报告也会一并删除，无法恢复。
        </Typography.Paragraph>
      </Modal>
    </Space>
  )
}
