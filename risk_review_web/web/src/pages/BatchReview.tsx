import { InboxOutlined } from '@ant-design/icons'
import {
  Alert,
  Button,
  Card,
  Checkbox,
  Col,
  Descriptions,
  Drawer,
  InputNumber,
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

import { AppError, cancelTask, fetchTask, parseExcel, runBatchReview } from '../api/reviewApi'
import type { AppNotice, BatchResultItem, BatchRow, TaskRecord } from '../types/review'
import { EmptyState } from '../components/EmptyState'
import { HeaderActions } from '../components/AppLayout'
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

const terminalStatuses = new Set(['done', 'error', 'cancelled'])

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

export function BatchReview({ onNotice }: BatchReviewProps) {
  const [rows, setRows] = useState<BatchRow[]>([])
  const [concurrency, setConcurrency] = useState(1)
  const [skipReviewer, setSkipReviewer] = useState(true)
  const [skipStageValidation, setSkipStageValidation] = useState(true)
  const [loading, setLoading] = useState(false)
  const [taskId, setTaskId] = useState<string>()
  const [task, setTask] = useState<TaskRecord<BatchTaskResult> | null>(null)
  const [running, setRunning] = useState(false)
  const [stopping, setStopping] = useState(false)
  const [submitting, setSubmitting] = useState(false)
  const [detailOpen, setDetailOpen] = useState(false)
  const [detailTitle, setDetailTitle] = useState('批量结果详情')
  const [detailContent, setDetailContent] = useState('')
  const pollTimerRef = useRef<number | null>(null)

  const resultItems = task?.result?.items || []
  const stats = useMemo(() => collectBatchStats(resultItems), [resultItems])
  const failedItems = useMemo(
    () => resultItems.filter((item) => item.status === 'error' || item.raw || item.skipReason),
    [resultItems]
  )

  useEffect(() => {
    return () => {
      if (pollTimerRef.current) window.clearTimeout(pollTimerRef.current)
    }
  }, [])

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

  const columns: ColumnsType<BatchRow> = [
    { title: '因子名', dataIndex: 'factorName' },
    { title: '需求内容', dataIndex: 'requirement', ellipsis: true },
    { title: '接口文档', dataIndex: 'apiDocs', ellipsis: true },
    { title: '因子加工输入字段', dataIndex: 'paramFields', ellipsis: true, render: (value?: string) => value || '-' },
    { title: '加工逻辑', dataIndex: 'procLogic', ellipsis: true },
    { title: '默认值', ellipsis: true, render: (_value, record) => record.defaultVal || record.defaultValues || '-' },
  ]

  async function beforeUpload(file: File) {
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
      setTask(response.task as TaskRecord<BatchTaskResult>)
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

  return (
    <Space className="app-page" direction="vertical" size={12} style={{ width: '100%' }}>
      <HeaderActions>
        <Space className="app-header-action-space" size={8} wrap>
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
        </Space>
      </HeaderActions>

      <Card className="compact-card" title="批量输入">
        <Upload.Dragger className="batch-upload-dragger" beforeUpload={beforeUpload} showUploadList={false} accept=".csv,.xlsx">
          <p className="ant-upload-drag-icon">
            <InboxOutlined />
          </p>
          <p className="ant-upload-text">拖拽 CSV / Excel 到这里，或点击选择文件</p>
          <p className="ant-upload-hint">上传后真实调用 `/api/parse-excel` 解析，提交后调用 `/api/review/batch` 执行。</p>
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

      <Drawer
        title={detailTitle}
        open={detailOpen}
        width="min(820px, 90vw)"
        onClose={() => setDetailOpen(false)}
      >
        <Typography.Paragraph style={{ whiteSpace: 'pre-wrap' }}>
          {detailContent || '暂无详细输出'}
        </Typography.Paragraph>
      </Drawer>
    </Space>
  )
}
