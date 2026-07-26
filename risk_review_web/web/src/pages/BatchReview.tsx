import { DeleteOutlined, DownloadOutlined, InboxOutlined } from '@ant-design/icons'
import {
  Alert,
  Button,
  Descriptions,
  Drawer,
  Empty,
  Modal,
  Progress,
  Space,
  Table,
  Tabs,
  Tag,
  Typography,
  Upload,
} from 'antd'
import { useEffect, useMemo, useRef, useState } from 'react'
import type { ColumnsType } from 'antd/es/table'

import { AppError, cancelTask, fetchTask, parseExcel, runBatchReview } from '../api/reviewApi'
import type { AppNotice, BatchResultItem, BatchRow, TaskRecord } from '../types/review'
import { EmptyState } from '../components/EmptyState'
import { HeaderActions, usePageActive } from '../components/AppLayout'
import { MarkdownViewer } from '../components/MarkdownViewer'
import { StatusBadge } from '../components/StatusBadge'
import { TaskStatusBadge } from '../components/TaskStatusBadge'
import { downloadBatchImportTemplate } from '../utils/csvTemplates'
import { parseReviewResult, taskFailureMessage } from '../utils/reviewTransform'

interface BatchReviewProps {
  onNotice: (notice: AppNotice) => void
}

interface BatchTaskResult {
  items?: BatchResultItem[]
  stdout?: string
  stderr?: string
  returncode?: number
}

interface BatchDisplayItem extends BatchRow, BatchResultItem {
  index: number
  apiNames: string[]
  importErrors: string[]
  conclusion?: string
  status: string
}

interface BatchSessionState {
  rows?: BatchRow[]
  taskId?: string
}

const terminalStatuses = new Set(['done', 'error', 'cancelled'])
const BATCH_SESSION_KEY = 'risk-review.batch-session.v1'

function readBatchSession(): BatchSessionState {
  try {
    const raw = window.sessionStorage.getItem(BATCH_SESSION_KEY)
    if (!raw) return {}
    const parsed = JSON.parse(raw) as BatchSessionState
    return parsed && typeof parsed === 'object' ? parsed : {}
  } catch {
    return {}
  }
}

function writeBatchSession(state: BatchSessionState) {
  try {
    window.sessionStorage.setItem(BATCH_SESSION_KEY, JSON.stringify(state))
  } catch {
    try {
      window.sessionStorage.setItem(BATCH_SESSION_KEY, JSON.stringify({ ...state, rows: [] }))
    } catch {
      // The active page remains usable even when browser storage is unavailable.
    }
  }
}

function collectBatchStats(items: BatchResultItem[]) {
  const total = items.length
  const done = items.filter((item) => item.status === 'done').length
  const failed = items.filter((item) => item.status === 'error').length
  const skipped = items.filter((item) => item.status === 'skip').length
  const running = items.filter((item) => item.status === 'running').length
  const queued = items.filter((item) => !item.status || item.status === 'queued').length
  const finished = done + failed + skipped
  return {
    total,
    done,
    failed,
    skipped,
    running,
    queued,
    finished,
    percent: total ? Math.round((finished / total) * 100) : 0,
  }
}

export function BatchReview({ onNotice }: BatchReviewProps) {
  const pageActive = usePageActive()
  const [rows, setRows] = useState<BatchRow[]>(() => readBatchSession().rows || [])
  const [loading, setLoading] = useState(false)
  const [taskId, setTaskId] = useState<string | undefined>(() => readBatchSession().taskId)
  const [task, setTask] = useState<TaskRecord<BatchTaskResult> | null>(null)
  const [running, setRunning] = useState(() => Boolean(readBatchSession().taskId))
  const [stopping, setStopping] = useState(false)
  const [submitting, setSubmitting] = useState(false)
  const [detailOpen, setDetailOpen] = useState(false)
  const [detailRecord, setDetailRecord] = useState<BatchDisplayItem | null>(null)
  const [clearConfirmOpen, setClearConfirmOpen] = useState(false)
  const pollTimerRef = useRef<number | null>(null)
  const inputLocked = running || submitting || stopping

  const resultItems = task?.result?.items || []
  const stats = useMemo(() => collectBatchStats(resultItems), [resultItems])
  const canClear = !inputLocked && !loading && Boolean(rows.length || task || taskId)

  useEffect(() => {
    return () => {
      if (pollTimerRef.current) window.clearTimeout(pollTimerRef.current)
    }
  }, [])

  useEffect(() => {
    const state: BatchSessionState = {
      rows,
      taskId,
    }
    writeBatchSession(state)
  }, [rows, taskId])

  useEffect(() => {
    if (!taskId || !running) return

    let cancelled = false
    const activeTaskId = taskId

    async function poll() {
      try {
        const nextTask = await fetchTask(activeTaskId) as TaskRecord<BatchTaskResult>
        if (cancelled) return

        setTask(nextTask)
        const status = String(nextTask.status || '').toLowerCase()
        if (terminalStatuses.has(status)) {
          setRunning(false)
          setStopping(false)
          if (status === 'done') {
            onNotice({ type: 'success', message: '批量评审完成' })
          } else if (status === 'cancelled') {
            onNotice({ type: 'warning', message: '批量评审已停止' })
          } else {
            onNotice({ type: 'error', message: taskFailureMessage(nextTask.result, '批量评审失败') })
          }
          return
        }

        pollTimerRef.current = window.setTimeout(poll, 1200)
      } catch (caught) {
        if (cancelled) return
        if (caught instanceof AppError && caught.status === 404) {
          setTaskId(undefined)
          setTask(null)
          setRunning(false)
          setStopping(false)
          onNotice({
            type: 'warning',
            message: '上一次批量任务已过期或后端已重启，已自动清理失效的任务记录',
          })
          return
        }
        const message = caught instanceof Error ? caught.message : '批量任务轮询失败'
        setRunning(false)
        setStopping(false)
        onNotice({ type: 'error', message })
      }
    }

    void poll()

    return () => {
      cancelled = true
      if (pollTimerRef.current) window.clearTimeout(pollTimerRef.current)
    }
  }, [taskId, running, onNotice])

  async function beforeUpload(file: File) {
    if (inputLocked) {
      onNotice({ type: 'warning', message: '当前批量任务正在执行，任务结束后才能导入新文件' })
      return false
    }

    setLoading(true)
    try {
      const buffer = await file.arrayBuffer()
      const binary = new Uint8Array(buffer)
      let raw = ''
      binary.forEach((item) => {
        raw += String.fromCharCode(item)
      })
      const base64 = btoa(raw)
      const parsed = await parseExcel(file.name, base64)
      setRows(parsed.items || [])
      setTask(null)
      setTaskId(undefined)
      setRunning(false)
      setStopping(false)
      onNotice({ type: 'success', message: `已导入 ${parsed.items?.length || 0} 条记录` })
    } catch (caught) {
      const message = caught instanceof AppError || caught instanceof Error ? caught.message : '解析文件失败'
      onNotice({ type: 'error', message })
    } finally {
      setLoading(false)
    }
    return false
  }

  async function handleStart() {
    if (!rows.length) {
      onNotice({ type: 'warning', message: '请先上传批量评审文件' })
      return
    }
    const invalidCount = rows.filter((row) => {
      const apiNames = row.apiNames || String(row.apiDocs || '').split(/[、,，]+/).filter(Boolean)
      return !String(row.factorName || '').trim()
        || !String(row.requirement || '').trim()
        || !apiNames.length
        || !String(row.procLogic || row.draft || '').trim()
    }).length
    if (invalidCount) {
      onNotice({ type: 'error', message: `存在 ${invalidCount} 条无效记录，请修正后重新导入` })
      return
    }

    setSubmitting(true)
    try {
      const response = await runBatchReview({ rows })
      setTaskId(response.taskId)
      const nextTask = response.task as TaskRecord<BatchTaskResult>
      setTask(nextTask)
      setRunning(true)
      setStopping(false)
      onNotice({ type: 'success', message: '批量评审任务已提交' })
    } catch (caught) {
      const message = caught instanceof Error ? caught.message : '批量评审提交失败'
      onNotice({ type: 'error', message })
    } finally {
      setSubmitting(false)
    }
  }

  async function handleStop() {
    if (!taskId || stopping) return

    setStopping(true)
    try {
      const nextTask = await cancelTask(taskId) as TaskRecord<BatchTaskResult>
      setTask(nextTask)
      if (terminalStatuses.has(String(nextTask.status || '').toLowerCase())) {
        setRunning(false)
        setStopping(false)
      }
      onNotice({ type: 'warning', message: '已请求终止批量评审，正在停止运行中的调用' })
    } catch (caught) {
      setStopping(false)
      const message = caught instanceof Error ? caught.message : '终止任务失败'
      onNotice({ type: 'error', message })
    }
  }

  function handleClear() {
    if (!canClear) return
    setRows([])
    setTask(null)
    setTaskId(undefined)
    setRunning(false)
    setStopping(false)
    setDetailOpen(false)
    setDetailRecord(null)
    setClearConfirmOpen(false)
    try {
      window.sessionStorage.removeItem(BATCH_SESSION_KEY)
    } catch {
      // State has already been cleared in memory; the normal persistence effect will overwrite storage.
    }
    onNotice({ type: 'success', message: '已清空当前未执行的批量记录' })
  }

  function exportResults() {
    if (!displayItems.length) return
    const safeCell = (value: unknown) => {
      const raw = String(value ?? '')
      const protectedValue = /^[=+\-@]/.test(raw) ? `'${raw}` : raw
      return `"${protectedValue.replace(/"/g, '""')}"`
    }
    const rows = [
      ['序号', '因子名称', '任务状态', '评审结论', '错误信息'],
      ...displayItems.map((item) => [
        item.index + 1,
        item.factorName || '',
        item.status || '',
        item.conclusion || '',
        item.status === 'error' ? item.raw || '执行失败' : item.importErrors.join('；'),
      ]),
    ]
    const csv = `\uFEFF${rows.map((row) => row.map(safeCell).join(',')).join('\n')}`
    const url = URL.createObjectURL(new Blob([csv], { type: 'text/csv;charset=utf-8' }))
    const anchor = document.createElement('a')
    anchor.href = url
    anchor.download = `批量评审结果-${new Date().toISOString().slice(0, 10)}.csv`
    anchor.click()
    URL.revokeObjectURL(url)
  }

  const displayItems: BatchDisplayItem[] = rows.map((row, index) => {
    const resultItem = resultItems.find((item) => (item.index ?? index) === index)
    const apiNames = row.apiNames || String(row.apiDocs || '').split(/[、,，]+/).map((value) => value.trim()).filter(Boolean)
    const errors = [
      !String(row.factorName || '').trim() ? '缺少因子名称' : '',
      !String(row.requirement || '').trim() ? '缺少需求内容' : '',
      !apiNames.length ? '缺少接口文档' : '',
      !String(row.procLogic || row.draft || '').trim() ? '缺少加工逻辑' : '',
    ].filter(Boolean)
    const parsed = resultItem?.raw ? parseReviewResult(resultItem.raw) : undefined
    return {
      ...row,
      ...resultItem,
      index,
      apiNames,
      importErrors: errors,
      conclusion: parsed?.conclusion,
      status: resultItem?.status || 'queued',
    }
  })
  const invalidCount = displayItems.filter((item) => item.importErrors.length).length
  const displayColumns: ColumnsType<(typeof displayItems)[number]> = [
    { title: '序号', width: 70, render: (_value, record) => record.index + 1 },
    {
      title: '案例 / 因子',
      width: 260,
      render: (_value, record) => (
        <Space direction="vertical" size={0}>
          <Typography.Text strong ellipsis={{ tooltip: record.caseName || record.factorName }}>
            {record.caseName || record.factorName || '-'}
          </Typography.Text>
          {record.caseName && record.caseName !== record.factorName ? (
            <Typography.Text type="secondary" ellipsis={{ tooltip: record.factorName }}>
              {record.factorName}
            </Typography.Text>
          ) : null}
        </Space>
      ),
    },
    {
      title: '加工逻辑摘要',
      dataIndex: 'procLogic',
      ellipsis: true,
      render: (value?: string, record?: (typeof displayItems)[number]) => {
        const text = String(value || record?.draft || '').replace(/\s+/g, ' ').trim()
        return text
          ? <Typography.Text ellipsis={{ tooltip: text }}>{text}</Typography.Text>
          : '-'
      },
    },
    {
      title: '导入校验',
      width: 160,
      render: (_value, record) => record.importErrors.length
        ? <Tag color="error">{record.importErrors.join('、')}</Tag>
        : <Tag color="success">有效</Tag>,
    },
    {
      title: '任务状态',
      width: 110,
      render: (_value, record) => <TaskStatusBadge value={record.status === 'skip' ? 'cancelled' : record.status} />,
    },
    {
      title: '评审结论',
      width: 110,
      render: (_value, record) => record.conclusion ? <StatusBadge value={record.conclusion} /> : '-',
    },
    {
      title: '操作',
      width: 100,
      fixed: 'right',
      render: (_value, record) => (
        <Button
          type="link"
          onClick={() => {
            setDetailRecord(record)
            setDetailOpen(true)
          }}
        >
          查看
        </Button>
      ),
    },
  ]

  return (
    <Space className="app-page batch-review-page" direction="vertical" size={12} style={{ width: '100%' }}>
      <HeaderActions page="batch-review">
        <Space className="app-header-action-space" size={8} wrap>
          <Button icon={<DownloadOutlined />} onClick={downloadBatchImportTemplate}>下载模板</Button>
          <Button icon={<DownloadOutlined />} disabled={!resultItems.length} onClick={exportResults}>导出结果</Button>
          <Button danger icon={<DeleteOutlined />} disabled={!canClear} onClick={() => setClearConfirmOpen(true)}>清空</Button>
        </Space>
      </HeaderActions>

      <section className="work-surface batch-import-surface">
        <Upload.Dragger
          className="batch-upload-dragger"
          beforeUpload={beforeUpload}
          showUploadList={false}
          accept=".csv,.xlsx"
          disabled={inputLocked || loading}
        >
          <p className="ant-upload-drag-icon"><InboxOutlined /></p>
          <p className="ant-upload-text">
            {inputLocked ? '当前批次执行中，任务结束后可重新导入' : '拖拽 CSV / Excel 到这里，或点击选择文件'}
          </p>
          <p className="ant-upload-hint">导入后先校验全部记录；存在无效行时不能开始执行。</p>
        </Upload.Dragger>
      </section>

      <section className="work-surface batch-work-surface">
        <div className="batch-command-bar">
          <Space size={12} wrap>
            <Typography.Text strong>批次明细</Typography.Text>
            <Tag>总数 {displayItems.length}</Tag>
            <Tag color={invalidCount ? 'error' : 'success'}>无效 {invalidCount}</Tag>
            <Tag color="success">完成 {stats.done}</Tag>
            <Tag color={stats.failed ? 'error' : 'default'}>失败 {stats.failed}</Tag>
            <Tag color="processing">执行中 {stats.running}</Tag>
          </Space>
          <Space size={8}>
            <Button
              type="primary"
              disabled={!rows.length || running || invalidCount > 0}
              loading={submitting}
              onClick={handleStart}
            >
              开始执行
            </Button>
            {running ? <Button danger loading={stopping} onClick={handleStop}>终止执行</Button> : null}
          </Space>
        </div>
        {task ? (
          <Progress
            className="batch-inline-progress"
            percent={stats.percent}
            status={stats.failed ? 'exception' : running ? 'active' : 'normal'}
          />
        ) : null}
        {String(task?.status || '').toLowerCase() === 'error' ? (
          <Alert type="error" showIcon message="批量任务执行失败" description={taskFailureMessage(task?.result, '批量评审失败')} />
        ) : null}
        {invalidCount ? (
          <Alert
            type="error"
            showIcon
            message={`存在 ${invalidCount} 条无效记录`}
            description="请修正源文件后重新导入；系统不会静默跳过无效记录。"
          />
        ) : null}
        {displayItems.length ? (
          <Table
            rowKey={(record) => String(record.index)}
            className="compact-table batch-status-table"
            columns={displayColumns}
            dataSource={displayItems}
            loading={loading}
            pagination={displayItems.length > 20 ? { pageSize: 20, size: 'small' } : false}
            scroll={{ x: 1180 }}
          />
        ) : (
          <EmptyState title="请先导入批量评审文件。" />
        )}
      </section>

      <Drawer
        title={detailRecord?.caseName || detailRecord?.factorName || '批量明细详情'}
        open={pageActive && detailOpen}
        width="min(960px, 90vw)"
        onClose={() => setDetailOpen(false)}
      >
        {detailRecord ? (
          <Tabs
            items={[
              {
                key: 'input',
                label: '输入详情',
                children: (
                  <Space direction="vertical" size={12} style={{ width: '100%' }}>
                    {detailRecord.importErrors.length ? (
                      <Alert type="error" showIcon message="导入校验未通过" description={detailRecord.importErrors.join('；')} />
                    ) : null}
                    {detailRecord.status === 'error' ? (
                      <Alert type="error" showIcon message="执行失败" description={detailRecord.raw || '未返回错误详情'} />
                    ) : null}
                    <Descriptions bordered column={1} size="small">
                      {detailRecord.caseName ? <Descriptions.Item label="案例名称">{detailRecord.caseName}</Descriptions.Item> : null}
                      <Descriptions.Item label="因子名称">{detailRecord.factorName || '-'}</Descriptions.Item>
                      <Descriptions.Item label="任务状态">
                        <TaskStatusBadge value={detailRecord.status === 'skip' ? 'cancelled' : detailRecord.status} />
                      </Descriptions.Item>
                      <Descriptions.Item label="评审结论">
                        {detailRecord.conclusion ? <StatusBadge value={detailRecord.conclusion} /> : '-'}
                      </Descriptions.Item>
                      <Descriptions.Item label="需求内容">
                        <Typography.Paragraph style={{ whiteSpace: 'pre-wrap', marginBottom: 0 }}>{detailRecord.requirement || '-'}</Typography.Paragraph>
                      </Descriptions.Item>
                      <Descriptions.Item label="接口文档">{detailRecord.apiNames.join('、') || '-'}</Descriptions.Item>
                      <Descriptions.Item label="因子加工输入字段">
                        <Typography.Paragraph style={{ whiteSpace: 'pre-wrap', marginBottom: 0 }}>{detailRecord.paramFields || '-'}</Typography.Paragraph>
                      </Descriptions.Item>
                      <Descriptions.Item label="加工逻辑">
                        <Typography.Paragraph style={{ whiteSpace: 'pre-wrap', marginBottom: 0 }}>{detailRecord.procLogic || '-'}</Typography.Paragraph>
                      </Descriptions.Item>
                      <Descriptions.Item label="默认值">
                        <Typography.Paragraph style={{ whiteSpace: 'pre-wrap', marginBottom: 0 }}>{detailRecord.defaultValues || detailRecord.defaultVal || '-'}</Typography.Paragraph>
                      </Descriptions.Item>
                      <Descriptions.Item label="待评审内容原文">
                        <Typography.Paragraph style={{ whiteSpace: 'pre-wrap', marginBottom: 0 }}>{detailRecord.draft || detailRecord.procLogic || '-'}</Typography.Paragraph>
                      </Descriptions.Item>
                      <Descriptions.Item label="备注">{detailRecord.remark || '-'}</Descriptions.Item>
                      <Descriptions.Item label="标准文件">{detailRecord.standardFileId || '-'}</Descriptions.Item>
                      <Descriptions.Item label="评审文件">{detailRecord.reviewFileId || '-'}</Descriptions.Item>
                    </Descriptions>
                  </Space>
                ),
              },
              {
                key: 'standard',
                label: '标准加工规范',
                children: detailRecord.standardText
                  ? <MarkdownViewer content={detailRecord.standardText} fileName={detailRecord.standardFileId || '标准加工规范.md'} />
                  : <Empty image={Empty.PRESENTED_IMAGE_SIMPLE} description="当前明细尚未生成标准加工规范" />,
              },
              {
                key: 'review',
                label: '评审结果',
                children: detailRecord.raw && detailRecord.status !== 'error'
                  ? <MarkdownViewer content={detailRecord.raw} fileName={detailRecord.reviewFileId || '评审结果.md'} />
                  : <Empty image={Empty.PRESENTED_IMAGE_SIMPLE} description="当前明细尚未生成评审结果" />,
              },
            ]}
          />
        ) : (
          <Empty image={Empty.PRESENTED_IMAGE_SIMPLE} description="暂无明细信息" />
        )}
      </Drawer>

      <Modal
        title="确认清空批量记录？"
        open={pageActive && clearConfirmOpen}
        maskClosable={false}
        onCancel={() => setClearConfirmOpen(false)}
        onOk={handleClear}
        okText="清空"
        cancelText="取消"
        okButtonProps={{ danger: true, disabled: !canClear }}
      >
        <Typography.Paragraph>
          将清除当前导入数据、任务 ID、结果汇总、结果明细和失败明细。
        </Typography.Paragraph>
        <Typography.Text type="secondary">
          已经写入 outputs/ 的历史产物不会删除。
        </Typography.Text>
      </Modal>
    </Space>
  )
}
