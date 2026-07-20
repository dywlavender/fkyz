import { Alert, Button, Card, Descriptions, Drawer, Empty, Space, Statistic, Table, Tag, Typography } from 'antd'
import { useEffect, useState } from 'react'
import type { ColumnsType } from 'antd/es/table'

import { cancelScheduledRun, fetchScheduledRun, fetchScheduledRuns, runScheduledTaskNow } from '../api/reviewApi'
import type { AppNotice, ScheduledRun, ScheduledRunItem, ScheduledTask } from '../types/review'

interface ScheduledTaskDetailProps {
  task: ScheduledTask
  onNotice: (notice: AppNotice) => void
  onRefreshTask: () => Promise<void> | void
}

function statusColor(status?: string) {
  const normalized = String(status || '').toLowerCase()
  if (normalized === 'success') return 'success'
  if (normalized === 'partial_success') return 'processing'
  if (normalized === 'running' || normalized === 'pending') return 'warning'
  if (normalized === 'failed') return 'error'
  return 'default'
}

function formatDate(value?: string) {
  if (!value) return '-'
  return new Date(value).toLocaleString('zh-CN', { hour12: false })
}

export function ScheduledTaskDetail(props: ScheduledTaskDetailProps) {
  const { task, onNotice, onRefreshTask } = props
  const [runs, setRuns] = useState<ScheduledRun[]>([])
  const [loading, setLoading] = useState(false)
  const [drawerOpen, setDrawerOpen] = useState(false)
  const [drawerTitle, setDrawerTitle] = useState('执行详情')
  const [selectedRun, setSelectedRun] = useState<ScheduledRun | null>(null)
  const [runningNow, setRunningNow] = useState(false)
  const [cancellingRunId, setCancellingRunId] = useState<string>()
  const hasActiveRun = runs.some((run) => run.status === 'pending' || run.status === 'running')

  async function loadRuns() {
    setLoading(true)
    try {
      const data = await fetchScheduledRuns(task.taskId)
      setRuns(data)
    } catch (caught) {
      const message = caught instanceof Error ? caught.message : '加载执行记录失败'
      onNotice({ type: 'error', message })
    } finally {
      setLoading(false)
    }
  }

  useEffect(() => {
    void loadRuns()
  }, [task.taskId])

  useEffect(() => {
    if (!hasActiveRun && selectedRun?.status !== 'pending' && selectedRun?.status !== 'running') return
    const timer = window.setInterval(() => {
      void fetchScheduledRuns(task.taskId).then(setRuns).catch(() => undefined)
      if (selectedRun?.runId) {
        void fetchScheduledRun(selectedRun.runId).then(setSelectedRun).catch(() => undefined)
      }
    }, 2000)
    return () => window.clearInterval(timer)
  }, [task.taskId, hasActiveRun, selectedRun?.runId, selectedRun?.status])

  useEffect(() => {
    if (!cancellingRunId) return
    const target = runs.find((run) => run.runId === cancellingRunId)
    if (target && !['pending', 'running'].includes(target.status)) setCancellingRunId(undefined)
  }, [cancellingRunId, runs])

  async function handleRunNow() {
    setRunningNow(true)
    try {
      const run = await runScheduledTaskNow(task.taskId)
      setSelectedRun(run)
      setDrawerTitle(`执行详情 ${run.runId}`)
      setDrawerOpen(true)
      onNotice({ type: 'success', message: `任务已提交执行${run.runId ? `：${run.runId}` : ''}` })
      await onRefreshTask()
      await loadRuns()
    } catch (caught) {
      const message = caught instanceof Error ? caught.message : '立即执行失败'
      onNotice({ type: 'error', message })
    } finally {
      setRunningNow(false)
    }
  }

  async function openRun(runId: string) {
    try {
      const detail = await fetchScheduledRun(runId)
      setSelectedRun(detail)
      setDrawerTitle(`执行详情 ${detail.runId}`)
      setDrawerOpen(true)
    } catch (caught) {
      const message = caught instanceof Error ? caught.message : '加载执行详情失败'
      onNotice({ type: 'error', message })
    }
  }

  async function handleCancelRun() {
    if (!selectedRun) return
    await terminateRun(selectedRun)
  }

  async function terminateRun(targetRun: ScheduledRun) {
    setCancellingRunId(targetRun.runId)
    try {
      const run = await cancelScheduledRun(targetRun.runId)
      if (selectedRun?.runId === targetRun.runId) setSelectedRun(run)
      onNotice({ type: 'warning', message: '已请求终止：排队项已取消，正在运行的 Claude 调用正在停止。' })
      await loadRuns()
    } catch (caught) {
      setCancellingRunId(undefined)
      onNotice({ type: 'error', message: caught instanceof Error ? caught.message : '终止执行失败' })
    }
  }

  const runColumns: ColumnsType<ScheduledRun> = [
    { title: '执行时间', dataIndex: 'startedAt', render: (value: string) => formatDate(value) },
    {
      title: '状态',
      dataIndex: 'status',
      render: (value: string) => <Tag color={statusColor(value)}>{value || '-'}</Tag>,
    },
    { title: '总数', dataIndex: 'total', width: 80 },
    { title: '成功', dataIndex: 'success', width: 80 },
    { title: '失败', dataIndex: 'failed', width: 80 },
    { title: '未完成', dataIndex: 'unknown', width: 100 },
    {
      title: '耗时',
      dataIndex: 'durationMs',
      width: 120,
      render: (value?: number) => (value ? `${(value / 1000).toFixed(1)}s` : '-'),
    },
    {
      title: '操作',
      width: 170,
      render: (_value, record) => (
        <Space size={4}>
          <Button type="link" onClick={() => openRun(record.runId)}>查看</Button>
          {['pending', 'running'].includes(record.status) ? (
            <Button danger type="link" loading={cancellingRunId === record.runId} onClick={() => void terminateRun(record)}>
              终止
            </Button>
          ) : null}
        </Space>
      ),
    },
  ]

  const itemColumns: ColumnsType<ScheduledRunItem> = [
    { title: '序号', dataIndex: 'index', width: 80 },
    { title: '因子名称', dataIndex: 'factorName' },
    {
      title: '状态',
      dataIndex: 'status',
      width: 120,
      render: (value: string) => <Tag color={statusColor(value)}>{value || '-'}</Tag>,
    },
    { title: 'standardFile', dataIndex: 'standardFile', ellipsis: true },
    { title: 'reviewFile', dataIndex: 'reviewFile', ellipsis: true },
    { title: '错误信息', dataIndex: 'error', ellipsis: true, render: (value?: string) => value || '-' },
  ]

  return (
    <Space className="compact-form" direction="vertical" size={12} style={{ width: '100%' }}>
      <Card
        className="compact-card"
        title="任务基本信息"
        extra={<Button size="small" type="primary" loading={runningNow} onClick={handleRunNow}>立即执行</Button>}
      >
        <Descriptions bordered column={2} size="small">
          <Descriptions.Item label="任务名称">{task.taskName}</Descriptions.Item>
          <Descriptions.Item label="状态">{task.enabled ? '启用' : '停用'}</Descriptions.Item>
          <Descriptions.Item label="执行模式">{task.mode}</Descriptions.Item>
          <Descriptions.Item label="运行模式">{task.runtimeMode}</Descriptions.Item>
          <Descriptions.Item label="执行周期">{task.scheduleType}</Descriptions.Item>
          <Descriptions.Item label="失败策略">{task.failurePolicy}</Descriptions.Item>
          <Descriptions.Item label="Reviewer">{(task.skipReviewer ?? true) ? '跳过' : '启用'}</Descriptions.Item>
          <Descriptions.Item label="阶段校验">{(task.skipStageValidation ?? true) ? '跳过' : '启用'}</Descriptions.Item>
          <Descriptions.Item label="并发数">{task.concurrency}</Descriptions.Item>
          <Descriptions.Item label="下次执行时间">{formatDate(task.nextRunAt)}</Descriptions.Item>
          <Descriptions.Item label="最近执行时间">{formatDate(task.lastRunAt)}</Descriptions.Item>
          <Descriptions.Item label="因子数量">{task.items.length}</Descriptions.Item>
          <Descriptions.Item label="最近结果">{task.lastRunStatus ? <Tag color={statusColor(task.lastRunStatus)}>{task.lastRunStatus}</Tag> : '-'}</Descriptions.Item>
        </Descriptions>
      </Card>

      <Card className="compact-card" title="因子列表">
        <Table
          rowKey={(record) => `${record.factorName}-${record.requirement}`}
          className="compact-table"
          size="small"
          dataSource={task.items}
          pagination={false}
          scroll={{ x: 960 }}
          columns={[
            { title: '因子名称', dataIndex: 'factorName' },
            { title: '需求', dataIndex: 'requirement', ellipsis: true },
            { title: 'reviewContent', dataIndex: 'reviewContent', ellipsis: true, render: (value?: string) => value || '-' },
            { title: 'standardFile', dataIndex: 'standardFile', ellipsis: true, render: (value?: string) => value || '-' },
            { title: '备注', dataIndex: 'remark', ellipsis: true, render: (value?: string) => value || '-' },
          ]}
        />
      </Card>

      <Card className="compact-card" title="执行记录">
        {!runs.length && !loading ? (
          <Empty image={Empty.PRESENTED_IMAGE_SIMPLE} description="暂无执行记录，后端暂未产生该任务的运行结果。" />
        ) : (
          <Table rowKey="runId" className="compact-table" size="small" loading={loading} dataSource={runs} columns={runColumns} pagination={{ pageSize: 8, size: 'small' }} scroll={{ x: 860 }} />
        )}
      </Card>

      <Drawer
        className="drawer-compact"
        title={drawerTitle}
        width="min(860px, 90vw)"
        open={drawerOpen}
        onClose={() => setDrawerOpen(false)}
      >
        <Space direction="vertical" size={12} style={{ width: '100%' }}>
          <Typography.Text type="secondary">
            {selectedRun ? `${selectedRun.taskName} · ${formatDate(selectedRun.startedAt)}` : '暂无内容'}
          </Typography.Text>
          {selectedRun && ['pending', 'running'].includes(selectedRun.status) ? (
            <Alert
              showIcon
              type="info"
              message="任务正在全局线程池中执行"
              action={<Button danger size="small" loading={cancellingRunId === selectedRun.runId} onClick={handleCancelRun}>终止执行</Button>}
            />
          ) : null}
          {selectedRun ? (
            <Space wrap size={12}>
              <Card className="compact-card"><Statistic title="总数" value={selectedRun.total} /></Card>
              <Card className="compact-card"><Statistic title="成功" value={selectedRun.success} /></Card>
              <Card className="compact-card"><Statistic title="失败" value={selectedRun.failed} /></Card>
              <Card className="compact-card"><Statistic title="未完成" value={selectedRun.unknown} /></Card>
            </Space>
          ) : null}
          <Table rowKey={(record) => `${record.index}-${record.factorName}`} className="compact-table" size="small" dataSource={selectedRun?.items || []} columns={itemColumns} pagination={false} scroll={{ x: 900 }} />
        </Space>
      </Drawer>
    </Space>
  )
}
