import { DeleteOutlined, DownloadOutlined, InboxOutlined, ReloadOutlined } from '@ant-design/icons'
import {
  Alert,
  Button,
  Card,
  Checkbox,
  Col,
  Descriptions,
  Drawer,
  InputNumber,
  Modal,
  Progress,
  Row,
  Space,
  Statistic,
  Table,
  Tag,
  Tooltip,
  Typography,
  Upload,
} from 'antd'
import { useEffect, useMemo, useRef, useState } from 'react'
import type { ColumnsType } from 'antd/es/table'

import { AppError, cancelTask, fetchTask, fetchTasks, parseExcel, runBatchReview } from '../api/reviewApi'
import type { AppNotice, BatchResultItem, BatchRow, TaskRecord } from '../types/review'
import { EmptyState } from '../components/EmptyState'
import { HeaderActions, usePageActive } from '../components/AppLayout'
import { downloadBatchImportTemplate } from '../utils/csvTemplates'
import { normalizeStatusText, taskFailureMessage } from '../utils/reviewTransform'

interface BatchReviewProps {
  onNotice: (notice: AppNotice) => void
}

interface BatchTaskResult {
  items?: BatchResultItem[]
  stdout?: string
  stderr?: string
  returncode?: number
}

interface BatchSessionState {
  rows?: BatchRow[]
  concurrency?: number
  skipReviewer?: boolean
  skipStageValidation?: boolean
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

function statusColor(status?: string) {
  const normalized = String(status || '').toLowerCase()
  if (normalized === 'done') return 'success'
  if (normalized === 'error') return 'error'
  if (normalized === 'skip') return 'default'
  if (normalized === 'running') return 'processing'
  if (normalized === 'queued') return 'warning'
  return 'default'
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

function formatTaskTime(value?: number) {
  if (!value) return '-'
  return new Date(value * 1000).toLocaleString('zh-CN', { hour12: false })
}

export function BatchReview({ onNotice }: BatchReviewProps) {
  const pageActive = usePageActive()
  const [rows, setRows] = useState<BatchRow[]>(() => readBatchSession().rows || [])
  const [concurrency, setConcurrency] = useState(() => readBatchSession().concurrency || 1)
  const [skipReviewer, setSkipReviewer] = useState(() => readBatchSession().skipReviewer ?? true)
  const [skipStageValidation, setSkipStageValidation] = useState(() => readBatchSession().skipStageValidation ?? true)
  const [loading, setLoading] = useState(false)
  const [taskId, setTaskId] = useState<string | undefined>(() => readBatchSession().taskId)
  const [task, setTask] = useState<TaskRecord<BatchTaskResult> | null>(null)
  const [running, setRunning] = useState(() => Boolean(readBatchSession().taskId))
  const [stopping, setStopping] = useState(false)
  const [submitting, setSubmitting] = useState(false)
  const [detailOpen, setDetailOpen] = useState(false)
  const [detailTitle, setDetailTitle] = useState('批量结果详情')
  const [detailContent, setDetailContent] = useState('')
  const [clearConfirmOpen, setClearConfirmOpen] = useState(false)
  const [batchHistory, setBatchHistory] = useState<TaskRecord<BatchTaskResult>[]>([])
  const [historyLoading, setHistoryLoading] = useState(false)
  const [historyTask, setHistoryTask] = useState<TaskRecord<BatchTaskResult> | null>(null)
  const [historyDetailOpen, setHistoryDetailOpen] = useState(false)
  const pollTimerRef = useRef<number | null>(null)
  const inputLocked = running || submitting || stopping

  const resultItems = task?.result?.items || []
  const stats = useMemo(() => collectBatchStats(resultItems), [resultItems])
  const failedItems = useMemo(
    () => resultItems.filter((item) => item.status === 'error' || item.raw || item.skipReason),
    [resultItems]
  )
  const canClear = !inputLocked && !loading && Boolean(rows.length || task || taskId)

  useEffect(() => {
    return () => {
      if (pollTimerRef.current) window.clearTimeout(pollTimerRef.current)
    }
  }, [])

  useEffect(() => {
    void loadBatchHistory(true)
  }, [])

  useEffect(() => {
    const state: BatchSessionState = {
      rows,
      concurrency,
      skipReviewer,
      skipStageValidation,
      taskId,
    }
    writeBatchSession(state)
  }, [rows, concurrency, skipReviewer, skipStageValidation, taskId])

  useEffect(() => {
    if (!taskId || !running) return

    let cancelled = false
    const activeTaskId = taskId

    async function poll() {
      try {
        const nextTask = await fetchTask(activeTaskId) as TaskRecord<BatchTaskResult>
        if (cancelled) return

        setTask(nextTask)
        updateBatchHistoryRecord(nextTask)
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

  function updateBatchHistoryRecord(nextTask: TaskRecord<BatchTaskResult>) {
    if (!nextTask.id) return
    setBatchHistory((current) => (
      [nextTask, ...current.filter((item) => item.id !== nextTask.id)]
        .sort((left, right) => (right.created || 0) - (left.created || 0))
    ))
    setHistoryTask((current) => current?.id === nextTask.id ? nextTask : current)
  }

  async function loadBatchHistory(silent = false) {
    setHistoryLoading(true)
    try {
      const tasks = await fetchTasks()
      const history = tasks
        .filter((item) => item.taskType === 'batch')
        .sort((left, right) => (right.created || 0) - (left.created || 0)) as TaskRecord<BatchTaskResult>[]
      setBatchHistory(history)
      setHistoryTask((current) => (
        current ? history.find((item) => item.id === current.id) || current : current
      ))
    } catch (caught) {
      if (!silent) {
        const message = caught instanceof Error ? caught.message : '批量历史加载失败'
        onNotice({ type: 'error', message })
      }
    } finally {
      setHistoryLoading(false)
    }
  }

  const columns: ColumnsType<BatchRow> = [
    { title: '因子名', dataIndex: 'factorName' },
    { title: '需求内容', dataIndex: 'requirement', ellipsis: true },
    { title: '接口文档', dataIndex: 'apiDocs', ellipsis: true },
    { title: '因子加工输入字段', dataIndex: 'paramFields', ellipsis: true, render: (value?: string) => value || '-' },
    { title: '加工逻辑', dataIndex: 'procLogic', ellipsis: true },
    { title: '默认值', ellipsis: true, render: (_value, record) => record.defaultVal || record.defaultValues || '-' },
  ]

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

    setSubmitting(true)
    try {
      const response = await runBatchReview({ rows, concurrency, skipReviewer, skipStageValidation })
      setTaskId(response.taskId)
      const nextTask = response.task as TaskRecord<BatchTaskResult>
      setTask(nextTask)
      updateBatchHistoryRecord(nextTask)
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
      updateBatchHistoryRecord(nextTask)
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
    setDetailTitle('批量结果详情')
    setDetailContent('')
    setClearConfirmOpen(false)
    try {
      window.sessionStorage.removeItem(BATCH_SESSION_KEY)
    } catch {
      // State has already been cleared in memory; the normal persistence effect will overwrite storage.
    }
    onNotice({ type: 'success', message: '已清空当前未执行的批量记录' })
  }

  function openResultDetail(item: BatchResultItem) {
    setDetailTitle(item.factorName || `第 ${(item.index ?? 0) + 1} 条结果`)
    setDetailContent(item.raw || item.skipReason || '暂无详细输出')
    setDetailOpen(true)
  }

  const resultColumns: ColumnsType<BatchResultItem> = [
    {
      title: '序号',
      width: 80,
      render: (_: unknown, record: BatchResultItem, index: number) => (record.index ?? index) + 1,
    },
    { title: '因子名', dataIndex: 'factorName', ellipsis: true },
    {
      title: '状态',
      dataIndex: 'status',
      width: 120,
      render: (value: BatchResultItem['status']) => (
        <Tag color={statusColor(value)}>{normalizeStatusText(value)}</Tag>
      ),
    },
    {
      title: '接口文档',
      dataIndex: 'apiNames',
      ellipsis: true,
      render: (value: BatchResultItem['apiNames']) => value?.join('、') || '-',
    },
    {
      title: '标准文件',
      dataIndex: 'standardFileId',
      ellipsis: true,
      render: (value: BatchResultItem['standardFileId']) => value || '-',
    },
    {
      title: '评审文件',
      dataIndex: 'reviewFileId',
      ellipsis: true,
      render: (value: BatchResultItem['reviewFileId']) => value || '-',
    },
    {
      title: '操作',
      width: 120,
      render: (_: unknown, record: BatchResultItem) => (
        <Tooltip title={record.raw || record.skipReason ? '查看该行后端返回的详细输出' : '该行暂无详细输出'}>
          <span>
            <Button type="link" disabled={!record.raw && !record.skipReason} onClick={() => openResultDetail(record)}>
              详情
            </Button>
          </span>
        </Tooltip>
      ),
    },
  ]

  const failureColumns: ColumnsType<BatchResultItem> = [
    {
      title: '序号',
      width: 80,
      render: (_: unknown, record: BatchResultItem, index: number) => (record.index ?? index) + 1,
    },
    { title: '因子名', dataIndex: 'factorName', ellipsis: true },
    {
      title: '状态',
      dataIndex: 'status',
      width: 120,
      render: (value: BatchResultItem['status']) => (
        <Tag color={statusColor(value)}>{normalizeStatusText(value)}</Tag>
      ),
    },
    {
      title: '失败原因',
      render: (_: unknown, record: BatchResultItem) => record.raw || record.skipReason || '-',
      ellipsis: true,
    },
    {
      title: '操作',
      width: 120,
      render: (_: unknown, record: BatchResultItem) => (
        <Button type="link" onClick={() => openResultDetail(record)}>查看</Button>
      ),
    },
  ]

  const historyColumns: ColumnsType<TaskRecord<BatchTaskResult>> = [
    {
      title: '批次 ID',
      dataIndex: 'id',
      width: 210,
      ellipsis: true,
      render: (value?: string) => value ? (
        <Typography.Text copyable={{ text: value }}>{value}</Typography.Text>
      ) : '-',
    },
    {
      title: '状态',
      dataIndex: 'status',
      width: 110,
      render: (value?: string) => (
        <Tag color={statusColor(value)}>{normalizeStatusText(value)}</Tag>
      ),
    },
    {
      title: '总数',
      width: 80,
      render: (_: unknown, record) => collectBatchStats(record.result?.items || []).total,
    },
    {
      title: '完成',
      width: 80,
      render: (_: unknown, record) => collectBatchStats(record.result?.items || []).done,
    },
    {
      title: '失败',
      width: 80,
      render: (_: unknown, record) => collectBatchStats(record.result?.items || []).failed,
    },
    {
      title: '执行中 / 排队',
      width: 130,
      render: (_: unknown, record) => {
        const recordStats = collectBatchStats(record.result?.items || [])
        return `${recordStats.running} / ${recordStats.queued}`
      },
    },
    {
      title: '创建时间',
      dataIndex: 'created',
      width: 180,
      render: (value?: number) => formatTaskTime(value),
    },
    {
      title: '更新时间',
      dataIndex: 'updated',
      width: 180,
      render: (value?: number) => formatTaskTime(value),
    },
    {
      title: '操作',
      fixed: 'right',
      width: 90,
      render: (_: unknown, record) => (
        <Button
          type="link"
          onClick={() => {
            setHistoryTask(record)
            setHistoryDetailOpen(true)
          }}
        >
          查看
        </Button>
      ),
    },
  ]

  return (
    <Space className="app-page" direction="vertical" size={12} style={{ width: '100%' }}>
      <HeaderActions page="batch-review">
        <Space className="app-header-action-space" size={8} wrap>
          <Button icon={<DownloadOutlined />} onClick={downloadBatchImportTemplate}>
            下载模板
          </Button>
          <Tooltip title={rows.length ? '提交后后端异步执行批量任务' : '请先上传并解析批量文件'}>
            <span>
              <Button type="primary" disabled={!rows.length || running} loading={submitting} onClick={handleStart}>
                开始批量评审
              </Button>
            </span>
          </Tooltip>
          <Tooltip title={running && taskId ? '调用后端取消当前批量任务' : '当前没有运行中的批量任务'}>
            <span>
              <Button danger disabled={!running || !taskId} loading={stopping} onClick={handleStop}>
                终止执行
              </Button>
            </span>
          </Tooltip>
          <Tooltip
            title={inputLocked || loading
              ? '当前批量任务或文件解析仍在进行，不能清空'
              : canClear
                ? '清空当前导入数据和已结束结果，不删除 outputs/ 历史产物'
                : '当前没有可清空的批量记录'}
          >
            <span>
              <Button
                danger
                icon={<DeleteOutlined />}
                disabled={!canClear}
                onClick={() => setClearConfirmOpen(true)}
              >
                清空
              </Button>
            </span>
          </Tooltip>
        </Space>
      </HeaderActions>

      <Card className="compact-card" title="批量输入">
        <Upload.Dragger
          className="batch-upload-dragger"
          beforeUpload={beforeUpload}
          showUploadList={false}
          accept=".csv,.xlsx"
          disabled={inputLocked || loading}
        >
          <p className="ant-upload-drag-icon">
            <InboxOutlined />
          </p>
          <p className="ant-upload-text">
            {inputLocked ? '当前批量任务执行中，暂不可导入新文件' : '拖拽 CSV / Excel 到这里，或点击选择文件'}
          </p>
          <p className="ant-upload-hint">
            {inputLocked
              ? '可继续查看进度或终止当前任务；任务结束后自动恢复文件导入。'
              : '上传后真实调用 `/api/parse-excel` 解析，提交后调用 `/api/review/batch` 执行。'}
          </p>
        </Upload.Dragger>
      </Card>

      <Card className="compact-card" title="任务配置">
        <Descriptions column={2} bordered size="small">
          <Descriptions.Item label="本批次并发上限">
            <InputNumber min={1} max={32} value={concurrency} disabled={running} onChange={(value: number | null) => setConcurrency(value || 1)} />
          </Descriptions.Item>
          <Descriptions.Item label="任务 ID">
            {taskId || '-'}
          </Descriptions.Item>
          <Descriptions.Item label="任务状态">
            <Tag color={statusColor(task?.status)}>{normalizeStatusText(task?.status)}</Tag>
          </Descriptions.Item>
          <Descriptions.Item label="全局池位置">
            {task?.poolStatus === 'queued' ? `排队第 ${task.queuePosition || '-'} 位` : task?.poolStatus === 'running' ? '执行中' : '-'}
          </Descriptions.Item>
          <Descriptions.Item label="运行开关" span={2}>
            <Space wrap>
              <Checkbox checked={skipReviewer} disabled={running} onChange={(event) => setSkipReviewer(event.target.checked)}>
                跳过 Reviewer
              </Checkbox>
              <Checkbox checked={skipStageValidation} disabled={running} onChange={(event) => setSkipStageValidation(event.target.checked)}>
                跳过阶段校验
              </Checkbox>
            </Space>
          </Descriptions.Item>
        </Descriptions>
        <Alert
          style={{ marginTop: 10 }}
          type="info"
          showIcon
          message="批量任务受全局执行池控制。"
          description="本批次并发上限不会突破全局并发；关闭页面不会取消后端任务。"
        />
      </Card>

      <Card className="compact-card" title="数据预览">
        {rows.length ? (
          <Table className="compact-table" rowKey={(_: BatchRow, index?: number) => String(index)} size="small" columns={columns} dataSource={rows} loading={loading} scroll={{ x: 1280 }} />
        ) : (
          <EmptyState title="暂无批量数据，先上传 CSV / Excel 文件。" />
        )}
      </Card>

      <Card className="compact-card" title="结果汇总">
        {task ? (
          <Space direction="vertical" size={12} style={{ width: '100%' }}>
            <Progress percent={stats.percent} status={stats.failed ? 'exception' : running ? 'active' : 'normal'} />
            <Row gutter={[12, 12]}>
              <Col xs={12} md={4}><Statistic title="总数" value={stats.total} /></Col>
              <Col xs={12} md={4}><Statistic title="完成" value={stats.done} valueStyle={{ color: '#2f8f57' }} /></Col>
              <Col xs={12} md={4}><Statistic title="失败" value={stats.failed} valueStyle={{ color: '#c63c35' }} /></Col>
              <Col xs={12} md={4}><Statistic title="跳过" value={stats.skipped} /></Col>
              <Col xs={12} md={4}><Statistic title="执行中" value={stats.running} /></Col>
              <Col xs={12} md={4}><Statistic title="排队" value={stats.queued} /></Col>
            </Row>
            {String(task.status || '').toLowerCase() === 'error' ? (
              <Alert type="error" showIcon message="任务错误" description={taskFailureMessage(task.result, '批量评审失败')} />
            ) : null}
          </Space>
        ) : (
          <Typography.Text type="secondary">
            任务提交后在这里展示执行进度和结果统计。
          </Typography.Text>
        )}
      </Card>

      <Card className="compact-card" title="结果明细">
        {resultItems.length ? (
          <Table
            rowKey={(record: BatchResultItem, index?: number) => `${record.index ?? index}-${record.factorName || ''}`}
            className="compact-table"
            columns={resultColumns}
            dataSource={resultItems}
            size="small"
            pagination={resultItems.length > 10 ? { pageSize: 10, size: 'small' } : false}
            scroll={{ x: 1100 }}
          />
        ) : (
          <EmptyState title="暂无批量结果，提交任务后开始展示。" />
        )}
      </Card>

      <Card className="compact-card" title="失败明细">
        {failedItems.length ? (
          <Table
            rowKey={(record: BatchResultItem, index?: number) => `${record.index ?? index}-${record.factorName || ''}`}
            className="compact-table"
            columns={failureColumns}
            dataSource={failedItems}
            size="small"
            pagination={failedItems.length > 8 ? { pageSize: 8, size: 'small' } : false}
            scroll={{ x: 920 }}
          />
        ) : (
          <EmptyState title="暂无失败或跳过记录。" />
        )}
      </Card>

      <Card
        className="compact-card"
        title="历史批次"
        extra={(
          <Button
            size="small"
            icon={<ReloadOutlined />}
            loading={historyLoading}
            onClick={() => void loadBatchHistory()}
          >
            刷新历史
          </Button>
        )}
      >
        <Alert
          style={{ marginBottom: 12 }}
          type="info"
          showIcon
          message="这里展示当前后端进程中的批量任务记录。"
          description="记录受后端任务保留时间配置限制，默认约保留 30 分钟，服务重启后不保留；已生成的准出和评审产物仍可在评审历史中查看。"
        />
        {batchHistory.length ? (
          <Table
            rowKey={(record) => record.id || `${record.created || 0}`}
            className="compact-table"
            columns={historyColumns}
            dataSource={batchHistory}
            loading={historyLoading}
            size="small"
            pagination={batchHistory.length > 10 ? { pageSize: 10, size: 'small' } : false}
            scroll={{ x: 1160 }}
          />
        ) : (
          <EmptyState title={historyLoading ? '正在加载批量历史…' : '暂无可查看的批量历史。'} />
        )}
      </Card>

      <Drawer
        title={detailTitle}
        open={pageActive && detailOpen}
        mask={false}
        width="min(820px, 90vw)"
        onClose={() => setDetailOpen(false)}
      >
        <Typography.Paragraph style={{ whiteSpace: 'pre-wrap' }}>
          {detailContent || '暂无详细输出'}
        </Typography.Paragraph>
      </Drawer>

      <Drawer
        title="批量历史详情"
        open={pageActive && historyDetailOpen}
        mask={false}
        width="min(1080px, 94vw)"
        onClose={() => setHistoryDetailOpen(false)}
      >
        {historyTask ? (
          <Space direction="vertical" size={12} style={{ width: '100%' }}>
            <Descriptions bordered size="small" column={2}>
              <Descriptions.Item label="批次 ID" span={2}>
                <Typography.Text copyable>{historyTask.id || '-'}</Typography.Text>
              </Descriptions.Item>
              <Descriptions.Item label="状态">
                <Tag color={statusColor(historyTask.status)}>
                  {normalizeStatusText(historyTask.status)}
                </Tag>
              </Descriptions.Item>
              <Descriptions.Item label="任务数">
                {collectBatchStats(historyTask.result?.items || []).total}
              </Descriptions.Item>
              <Descriptions.Item label="创建时间">
                {formatTaskTime(historyTask.created)}
              </Descriptions.Item>
              <Descriptions.Item label="更新时间">
                {formatTaskTime(historyTask.updated)}
              </Descriptions.Item>
            </Descriptions>
            {historyTask.result?.items?.length ? (
              <Table
                rowKey={(record: BatchResultItem, index?: number) => `${record.index ?? index}-${record.factorName || ''}`}
                className="compact-table"
                columns={resultColumns}
                dataSource={historyTask.result.items}
                size="small"
                pagination={historyTask.result.items.length > 10 ? { pageSize: 10, size: 'small' } : false}
                scroll={{ x: 1100 }}
              />
            ) : (
              <EmptyState title="该批次暂时没有可展示的结果明细。" />
            )}
          </Space>
        ) : (
          <EmptyState title="未选择批量历史记录。" />
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
          并发数和运行开关会保留，已经写入 outputs/ 的历史产物不会删除。
        </Typography.Text>
      </Modal>
    </Space>
  )
}
