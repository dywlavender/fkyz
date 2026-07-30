import { Alert, Button, Col, Form, Input, Modal, Row, Select, Space, Spin, Tabs, Tag, Timeline, Typography } from 'antd'
import { useMemo, useRef, useState } from 'react'

import { cancelTask, fetchApiDocDetail, fetchOutputText, fetchTask, runRequirementReview } from '../../api/reviewApi'
import { usePageActive } from '../../components/AppLayout'
import { EmptyState } from '../../components/EmptyState'
import { MarkdownViewer } from '../../components/MarkdownViewer'
import { ApiDocumentDrawer } from '../single-review'
import type { ApiDocItem, RequirementReviewResponse, TaskRecord } from '../../types/review'
import type { RequirementExecutionLog, RequirementReviewFormValues, RequirementReviewPageProps } from './types'
import { taskLogTime } from './types'

const TERMINAL_STATUSES = new Set(['done', 'error', 'cancelled'])

function RequirementExecutionLogModal({ active, open, logs, onClose }: { active: boolean; open: boolean; logs: RequirementExecutionLog[]; onClose: () => void }) {
  const runtimeLogs = [...logs].reverse().filter((item) => !item.isDebug)
  const debugLogs = [...logs].reverse().filter((item) => item.isDebug)
  const renderLogs = (items: RequirementExecutionLog[]) => items.length ? (
    <Timeline items={items.map((item) => ({
      color: item.level === 'error' ? 'red' : item.level === 'warning' ? 'orange' : item.level === 'success' ? 'green' : 'blue',
      children: (
        <Space direction="vertical" size={4} style={{ width: '100%' }}>
          <Space size={6} wrap>
            <Typography.Text type="secondary">{item.time}</Typography.Text>
            <Tag color={item.level === 'error' ? 'red' : item.level === 'warning' ? 'orange' : item.level === 'success' ? 'green' : 'blue'}>需求评审</Tag>
            {item.stream ? <Tag>{item.stream}</Tag> : null}
          </Space>
          <Typography.Text>{item.message}</Typography.Text>
          {item.detail ? <pre className={item.isDebug ? 'single-debug-log-pre' : 'single-log-detail-pre'}>{item.detail}</pre> : null}
        </Space>
      ),
    }))} />
  ) : <EmptyState title="暂无执行日志。" />
  return (
    <Modal title="执行日志" open={active && open} maskClosable={false} width="min(960px, 92vw)" footer={null} onCancel={onClose}>
      <Tabs size="small" items={[
        {
          key: 'runtime',
          label: `运行日志（${runtimeLogs.length}）`,
          children: <div className="execution-log-modal-body">{renderLogs(runtimeLogs)}</div>,
        },
        {
          key: 'debug',
          label: `模型输入/输出/工具调用（${debugLogs.length}）`,
          children: <div className="execution-log-modal-body">{renderLogs(debugLogs)}</div>,
        },
      ]} />
    </Modal>
  )
}

export function RequirementReviewPage({ apiDocs, onNotice }: RequirementReviewPageProps) {
  const pageActive = usePageActive()
  const [form] = Form.useForm<RequirementReviewFormValues>()
  const values = Form.useWatch([], form)
  const [loading, setLoading] = useState(false)
  const [stopping, setStopping] = useState(false)
  const [taskId, setTaskId] = useState<string>()
  const [report, setReport] = useState('')
  const [reviewFileId, setReviewFileId] = useState<string>()
  const [error, setError] = useState<string>()
  const [logs, setLogs] = useState<RequirementExecutionLog[]>([])
  const [logOpen, setLogOpen] = useState(false)
  const [apiDocOpen, setApiDocOpen] = useState(false)
  const [apiDocLoading, setApiDocLoading] = useState(false)
  const [apiDocDetail, setApiDocDetail] = useState<ApiDocItem | null>(null)
  const runTokenRef = useRef(0)

  const status = useMemo(() => {
    if (stopping) return { label: '停止中', color: 'orange' }
    if (loading) return { label: '评审中', color: 'processing' }
    if (error) return { label: '执行失败', color: 'error' }
    if (report) return { label: '已完成', color: 'success' }
    return { label: '待执行', color: 'default' }
  }, [error, loading, report, stopping])

  function appendLog(level: RequirementExecutionLog['level'], message: string, detail?: string) {
    const now = new Date()
    setLogs((current) => [...current, {
      id: `${now.getTime()}-${Math.random().toString(36).slice(2, 8)}`,
      time: now.toLocaleTimeString(),
      level,
      message,
      detail,
    }])
  }

  function syncTaskLogs(currentTaskId: string, task: TaskRecord<RequirementReviewResponse>) {
    const runtime = (task.logs || []).map((item, index) => ({
      id: `${currentTaskId}-runtime-${item.time ?? index}-${index}`,
      time: taskLogTime(item),
      level: 'info' as const,
      message: item.message || '任务状态更新',
    }))
    const debug = (task.debugLogs || []).map((item, index) => {
      const stream = String(item.stream || '').toLowerCase()
      const labels: Record<string, string> = { prompt: '模型实际输入', stdout: '模型最终输出', stderr: '模型错误输出' }
      return {
        id: `${currentTaskId}-debug-${item.time ?? index}-${index}`,
        time: taskLogTime(item),
        level: (stream === 'stderr' && item.message ? 'error' : 'info') as RequirementExecutionLog['level'],
        message: labels[stream] || 'Agent 交互',
        detail: item.message || '',
        stream,
        isDebug: true,
      }
    })
    setLogs((current) => {
      const ids = new Set(current.map((item) => item.id))
      const next = [...runtime, ...debug].filter((item) => !ids.has(item.id))
      return next.length ? [...current, ...next] : current
    })
  }

  async function pollTask(currentTaskId: string, runToken: number) {
    const started = Date.now()
    while (Date.now() - started < 10 * 60 * 1000) {
      if (runTokenRef.current !== runToken) throw new Error('执行已停止')
      const task = await fetchTask(currentTaskId) as TaskRecord<RequirementReviewResponse>
      syncTaskLogs(currentTaskId, task)
      if (TERMINAL_STATUSES.has(String(task.status || '').toLowerCase())) return task
      await new Promise((resolve) => window.setTimeout(resolve, 1200))
    }
    throw new Error('任务轮询超时，请稍后重试。')
  }

  async function openSelectedApiDoc(name?: string) {
    const selectedName = name || form.getFieldValue('apiNames')?.[0]
    if (!selectedName) {
      onNotice({ type: 'warning', message: '请先选择接口文档' })
      return
    }
    setApiDocOpen(true)
    setApiDocLoading(true)
    try {
      setApiDocDetail(await fetchApiDocDetail(selectedName))
    } catch (caught) {
      onNotice({ type: 'error', message: caught instanceof Error ? caught.message : '接口文档读取失败' })
    } finally {
      setApiDocLoading(false)
    }
  }

  async function startReview() {
    const formValues = await form.validateFields()
    const runToken = ++runTokenRef.current
    setLoading(true)
    setStopping(false)
    setTaskId(undefined)
    setReport('')
    setReviewFileId(undefined)
    setError(undefined)
    setLogs([])
    appendLog('info', '正在提交需求评审任务')
    try {
      const submitted = await runRequirementReview(formValues)
      if (runTokenRef.current !== runToken) {
        void cancelTask(submitted.taskId)
        return
      }
      setTaskId(submitted.taskId)
      syncTaskLogs(submitted.taskId, submitted.task)
      appendLog('success', '需求评审任务提交成功', submitted.taskId)
      const task = await pollTask(submitted.taskId, runToken)
      if (runTokenRef.current !== runToken) return
      const result = task.result || {}
      if (String(task.status).toLowerCase() === 'cancelled') throw new Error('需求评审已停止')
      if (String(task.status).toLowerCase() !== 'done') throw new Error(result.stderr || '需求评审执行失败')
      let markdown = result.stdout || ''
      if (result.reviewFileId) {
        markdown = await fetchOutputText(result.reviewFileId)
        setReviewFileId(result.reviewFileId)
      }
      setReport(markdown)
      appendLog('success', '需求评审报告读取完成', result.reviewFileId)
      onNotice({ type: 'success', message: '需求评审已完成' })
    } catch (caught) {
      if (runTokenRef.current !== runToken) return
      const message = caught instanceof Error ? caught.message : '需求评审执行失败'
      setError(message)
      appendLog(message.includes('停止') ? 'warning' : 'error', message)
      if (!message.includes('停止')) onNotice({ type: 'error', message })
    } finally {
      if (runTokenRef.current === runToken) setLoading(false)
    }
  }

  async function stopReview() {
    if (!loading) return
    setStopping(true)
    ++runTokenRef.current
    appendLog('warning', '已请求停止需求评审')
    try {
      if (taskId) await cancelTask(taskId)
      setLoading(false)
      onNotice({ type: 'warning', message: '已请求停止需求评审' })
    } catch (caught) {
      const message = caught instanceof Error ? caught.message : '停止需求评审失败'
      setError(message)
      appendLog('error', '停止需求评审失败', message)
    } finally {
      setStopping(false)
    }
  }

  function clearPage() {
    if (loading) return
    ++runTokenRef.current
    form.resetFields()
    setTaskId(undefined)
    setReport('')
    setReviewFileId(undefined)
    setError(undefined)
    setLogs([])
    setLogOpen(false)
  }

  return (
    <Space className="app-page compact-form requirement-review-page" direction="vertical" size={12} style={{ width: '100%' }}>
      <Row gutter={[0, 16]} align="stretch">
        <Col span={24}>
          <section className="work-surface requirement-review-input-surface" aria-label="需求评审输入">
            <div className="surface-heading">
              <div>
                <Typography.Title level={5}>评审输入</Typography.Title>
                <Typography.Text type="secondary">检查需求目标、核心业务口径及数据来源是否存在阻断问题。</Typography.Text>
              </div>
              <Button size="small" danger disabled={loading} onClick={clearPage}>清空</Button>
            </div>
            <Form form={form} layout="vertical" initialValues={{ apiNames: [], requirement: '' }}>
              <Form.Item name="apiNames" label={<Space size={8}><span>关联接口文档</span><Button type="link" size="small" onClick={() => void openSelectedApiDoc()}>查看已选接口</Button></Space>} rules={[{ required: true, message: '至少选择一个接口文档' }]}>
                <Select disabled={loading} mode="multiple" showSearch optionFilterProp="label" placeholder="搜索并选择接口文档" options={apiDocs.map((item) => ({ label: `${item.name}${item.category ? ` · ${item.category}` : ''}`, value: item.name }))} />
              </Form.Item>
              <Form.Item name="requirement" label="需求内容" rules={[{ required: true, whitespace: true, message: '请输入需求内容' }]}>
                <Input.TextArea disabled={loading} rows={9} showCount maxLength={50000} placeholder="输入需求目标、核心加工对象、统计范围和主要判断规则；实现细节可由后续标准生成阶段补充" />
              </Form.Item>
              <div className="single-primary-actions">
                <Button size="large" type="primary" loading={loading && !stopping} disabled={stopping} onClick={() => void startReview()}>{loading ? '评审中...' : '开始评审'}</Button>
                {loading ? <Button danger loading={stopping} onClick={() => void stopReview()}>终止执行</Button> : null}
                <Button disabled={!logs.length} onClick={() => setLogOpen(true)}>执行详情</Button>
                <Tag color={status.color}>{status.label}</Tag>
              </div>
            </Form>
            {error ? <Alert className="single-inline-error" type="error" showIcon message="评审执行失败" description={error} action={<Button size="small" onClick={() => setLogOpen(true)}>查看执行详情</Button>} /> : null}
          </section>
        </Col>
        <Col span={24}>
          <section className="work-surface requirement-review-result-surface" aria-label="需求评审结果">
            <div className="surface-heading">
              <div>
                <Typography.Title level={5}>评审结果</Typography.Title>
                <Typography.Text type="secondary">评审完成后直接展示完整 Markdown 报告。</Typography.Text>
              </div>
              {reviewFileId ? <Button href={`/outputs/${encodeURIComponent(reviewFileId)}`} download="requirement_review.md">下载评审报告</Button> : null}
            </div>
            <div className="requirement-review-result-content">
              {loading ? <Spin /> : report ? <MarkdownViewer content={report} fileName="requirement_review.md" /> : <EmptyState title="选择接口、填写需求并开始评审。" />}
            </div>
          </section>
        </Col>
      </Row>
      <ApiDocumentDrawer active={pageActive} open={apiDocOpen} loading={apiDocLoading} detail={apiDocDetail} selectedNames={values?.apiNames || []} onClose={() => setApiDocOpen(false)} onSelect={(name) => void openSelectedApiDoc(name)} />
      <RequirementExecutionLogModal active={pageActive} open={logOpen} logs={logs} onClose={() => setLogOpen(false)} />
    </Space>
  )
}
