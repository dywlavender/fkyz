import {
  CheckCircleOutlined,
  CloseCircleOutlined,
  EyeOutlined,
  FileSearchOutlined,
  PauseCircleOutlined,
  PlayCircleOutlined,
  ReloadOutlined,
} from '@ant-design/icons'
import {
  Alert,
  Button,
  Card,
  Col,
  Descriptions,
  Empty,
  Modal,
  Progress,
  Row,
  Select,
  Space,
  Spin,
  Statistic,
  Table,
  Tag,
  Typography,
} from 'antd'
import type { ColumnsType } from 'antd/es/table'
import { useCallback, useEffect, useMemo, useState, type Key } from 'react'

import {
  cancelTask,
  createReviewModeExperiment,
  fetchOutputText,
  fetchReviewModeExperiment,
  fetchReviewModeExperiments,
  fetchValidationCases,
} from '../api/reviewApi'
import { HeaderActions, usePageActive } from '../components/AppLayout'
import { MarkdownViewer } from '../components/MarkdownViewer'
import type {
  AppNotice,
  ReviewModeExperiment,
  ReviewModeExperimentItem,
  ReviewModeMetric,
  ReviewModeResult,
  ValidationCase,
} from '../types/review'

interface ReviewModeExperimentPageProps {
  onNotice: (notice: AppNotice) => void
}

const ACTIVE_EXPERIMENT_KEY = 'risk-review-active-mode-experiment'

function isActive(status?: string) {
  return ['queued', 'running', 'cancelling'].includes(String(status || '').toLowerCase())
}

function formatDate(value?: string | null) {
  if (!value) return '-'
  const date = new Date(value)
  return Number.isNaN(date.getTime()) ? value : date.toLocaleString('zh-CN', { hour12: false })
}

function conclusionLabel(value?: string) {
  if (value === 'pass') return '通过'
  if (value === 'fail') return '不符合'
  if (value === 'unreviewable') return '无法评审'
  return value || '-'
}

function conclusionColor(value?: string) {
  if (value === 'pass') return 'success'
  if (value === 'fail') return 'error'
  if (value === 'unreviewable') return 'warning'
  return 'default'
}

function statusTag(status?: string) {
  if (status === 'done') return <Tag color="success">已完成</Tag>
  if (status === 'cancelled') return <Tag>已终止</Tag>
  if (status === 'error') return <Tag color="error">失败</Tag>
  if (status === 'cancelling') return <Tag color="warning">终止中</Tag>
  if (status === 'running') return <Tag color="processing">执行中</Tag>
  return <Tag>排队中</Tag>
}

function matchTag(result: ReviewModeResult, baselineConfigured: boolean) {
  if (result.status === 'error') return <Tag color="error">执行失败</Tag>
  if (result.status === 'cancelled') return <Tag>已终止</Tag>
  if (result.status !== 'success') return <Tag color="processing">{result.status === 'running' ? '执行中' : '待执行'}</Tag>
  if (!result.conclusionMatched) return <Tag color="error" icon={<CloseCircleOutlined />}>结论未命中</Tag>
  if (baselineConfigured && !result.issuesMatched) return <Tag color="warning" icon={<CloseCircleOutlined />}>问题未命中</Tag>
  return <Tag color="success" icon={<CheckCircleOutlined />}>基准命中</Tag>
}

function accuracy(value?: number | null) {
  return value == null ? '-' : `${value.toFixed(1)}%`
}

function duration(value?: number | null) {
  if (value == null) return '-'
  if (value < 1000) return `${value} ms`
  return `${(value / 1000).toFixed(1)} s`
}

function metricDescription(metric: ReviewModeMetric) {
  return `结论 ${accuracy(metric.conclusionAccuracy)} · 问题 ${accuracy(metric.issueAccuracy)} · 平均 ${duration(metric.averageDurationMs)}`
}

export function ReviewModeExperimentPage({ onNotice }: ReviewModeExperimentPageProps) {
  const pageActive = usePageActive()
  const [cases, setCases] = useState<ValidationCase[]>([])
  const [experiments, setExperiments] = useState<ReviewModeExperiment[]>([])
  const [selectedCaseIds, setSelectedCaseIds] = useState<Key[]>([])
  const [repetitions, setRepetitions] = useState(1)
  const [activeExperiment, setActiveExperiment] = useState<ReviewModeExperiment | null>(null)
  const [loading, setLoading] = useState(true)
  const [submitting, setSubmitting] = useState(false)
  const [cancelling, setCancelling] = useState(false)
  const [documentOpen, setDocumentOpen] = useState(false)
  const [documentTitle, setDocumentTitle] = useState('')
  const [documentText, setDocumentText] = useState('')
  const [documentLoading, setDocumentLoading] = useState(false)

  const loadAll = useCallback(async () => {
    setLoading(true)
    try {
      const [caseList, experimentList] = await Promise.all([
        fetchValidationCases(),
        fetchReviewModeExperiments(),
      ])
      setCases(caseList)
      setExperiments(experimentList)
      const storedId = window.localStorage.getItem(ACTIVE_EXPERIMENT_KEY)
      const stored = experimentList.find((item) => item.experimentId === storedId)
      if (stored) setActiveExperiment(stored)
      else if (!activeExperiment && experimentList.length) setActiveExperiment(experimentList[0])
    } catch (caught) {
      onNotice({ type: 'error', message: caught instanceof Error ? caught.message : '加载对比实验失败' })
    } finally {
      setLoading(false)
    }
  }, [activeExperiment, onNotice])

  useEffect(() => {
    void loadAll()
    // Initial bootstrap only. Subsequent refreshes are explicit or polling-driven.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [])

  useEffect(() => {
    if (!pageActive || !activeExperiment || !isActive(activeExperiment.status)) return
    let disposed = false
    async function refresh() {
      try {
        const next = await fetchReviewModeExperiment(activeExperiment!.experimentId)
        if (disposed) return
        setActiveExperiment(next)
        setExperiments((current) => [next, ...current.filter((item) => item.experimentId !== next.experimentId)])
        if (!isActive(next.status)) {
          window.localStorage.removeItem(ACTIVE_EXPERIMENT_KEY)
          setCancelling(false)
        }
      } catch (caught) {
        if (!disposed) onNotice({ type: 'error', message: caught instanceof Error ? caught.message : '刷新实验进度失败' })
      }
    }
    void refresh()
    const timer = window.setInterval(refresh, 1500)
    return () => {
      disposed = true
      window.clearInterval(timer)
    }
  }, [activeExperiment?.experimentId, activeExperiment?.status, onNotice, pageActive])

  const progress = useMemo(() => {
    const total = activeExperiment?.summary?.total || 0
    const completed = activeExperiment?.summary?.completed || 0
    return total ? Math.round((completed / total) * 100) : 0
  }, [activeExperiment])

  async function startExperiment() {
    if (!selectedCaseIds.length) {
      onNotice({ type: 'warning', message: '请至少选择一个验证案例' })
      return
    }
    setSubmitting(true)
    try {
      const response = await createReviewModeExperiment({
        caseIds: selectedCaseIds.map(String),
        repetitions,
      })
      const experiment = await fetchReviewModeExperiment(response.experimentId)
      setActiveExperiment(experiment)
      setExperiments((current) => [experiment, ...current.filter((item) => item.experimentId !== experiment.experimentId)])
      window.localStorage.setItem(ACTIVE_EXPERIMENT_KEY, experiment.experimentId)
      onNotice({ type: 'success', message: `已提交 ${selectedCaseIds.length} 个案例的模式对比实验` })
    } catch (caught) {
      onNotice({ type: 'error', message: caught instanceof Error ? caught.message : '提交对比实验失败' })
    } finally {
      setSubmitting(false)
    }
  }

  async function stopExperiment() {
    if (!activeExperiment?.taskId || !isActive(activeExperiment.status)) return
    setCancelling(true)
    try {
      await cancelTask(activeExperiment.taskId)
      const next = await fetchReviewModeExperiment(activeExperiment.experimentId)
      setActiveExperiment(next)
      onNotice({ type: 'warning', message: '已请求终止对比实验' })
    } catch (caught) {
      setCancelling(false)
      onNotice({ type: 'error', message: caught instanceof Error ? caught.message : '终止实验失败' })
    }
  }

  async function openDocument(fileId: string | undefined, title: string) {
    if (!fileId) return
    setDocumentTitle(title)
    setDocumentText('')
    setDocumentOpen(true)
    setDocumentLoading(true)
    try {
      setDocumentText(await fetchOutputText(fileId))
    } catch (caught) {
      setDocumentText(`# 文档读取失败\n\n${caught instanceof Error ? caught.message : '未知错误'}`)
    } finally {
      setDocumentLoading(false)
    }
  }

  const caseColumns: ColumnsType<ValidationCase> = [
    { title: '案例名称', dataIndex: 'caseName', ellipsis: true },
    { title: '因子名称', dataIndex: 'factorName', ellipsis: true },
    {
      title: '期望结论',
      dataIndex: 'expectedConclusion',
      width: 110,
      render: (value?: string) => <Tag color={conclusionColor(value)}>{conclusionLabel(value)}</Tag>,
    },
    {
      title: '问题级基准',
      width: 120,
      render: (_value, record) => (
        record.expectedIssuePatterns?.length
        || record.forbiddenIssuePatterns?.length
        || record.maxIssueCount != null
      ) ? <Tag color="blue">已配置</Tag> : <Tag>仅比较结论</Tag>,
    },
  ]

  const resultColumns: ColumnsType<ReviewModeExperimentItem> = [
    { title: '案例', dataIndex: 'caseName', ellipsis: true, width: 190 },
    { title: '轮次', dataIndex: 'round', width: 70, align: 'center' },
    {
      title: '期望',
      dataIndex: 'expectedConclusion',
      width: 90,
      render: (value?: string) => <Tag color={conclusionColor(value)}>{conclusionLabel(value)}</Tag>,
    },
    {
      title: '直接评审',
      width: 210,
      render: (_value, record) => (
        <Space direction="vertical" size={3}>
          <Space size={5}>
            <Tag color={conclusionColor(record.direct.actualConclusion)}>{conclusionLabel(record.direct.actualConclusion)}</Tag>
            {matchTag(record.direct, Boolean(record.issueBaselineConfigured))}
          </Space>
          <Typography.Text type="secondary">{duration(record.direct.durationMs)}</Typography.Text>
          {record.direct.error ? <Typography.Text type="danger">{record.direct.error}</Typography.Text> : null}
        </Space>
      ),
    },
    {
      title: '两阶段评审',
      width: 210,
      render: (_value, record) => (
        <Space direction="vertical" size={3}>
          <Space size={5}>
            <Tag color={conclusionColor(record.twoStage.actualConclusion)}>{conclusionLabel(record.twoStage.actualConclusion)}</Tag>
            {matchTag(record.twoStage, Boolean(record.issueBaselineConfigured))}
          </Space>
          <Typography.Text type="secondary">{duration(record.twoStage.durationMs)}</Typography.Text>
          {record.twoStage.error ? <Typography.Text type="danger">{record.twoStage.error}</Typography.Text> : null}
        </Space>
      ),
    },
    {
      title: '文档',
      width: 250,
      render: (_value, record) => (
        <Space size={0} wrap>
          <Button type="link" disabled={!record.direct.reviewFile} onClick={() => openDocument(record.direct.reviewFile, '直接评审报告')}>
            直接评审
          </Button>
          <Button type="link" disabled={!record.twoStage.standardFile} onClick={() => openDocument(record.twoStage.standardFile, '两阶段标准文档')}>
            标准文档
          </Button>
          <Button type="link" disabled={!record.twoStage.reviewFile} onClick={() => openDocument(record.twoStage.reviewFile, '两阶段评审报告')}>
            评审报告
          </Button>
        </Space>
      ),
    },
  ]

  const historyColumns: ColumnsType<ReviewModeExperiment> = [
    { title: '实验编号', dataIndex: 'experimentId', ellipsis: true, width: 210 },
    { title: '案例数', dataIndex: 'caseCount', width: 80 },
    { title: '重复次数', dataIndex: 'repetitions', width: 90 },
    {
      title: '直接评审命中率',
      width: 140,
      render: (_value, record) => accuracy(record.summary?.direct?.fullAccuracy),
    },
    {
      title: '两阶段命中率',
      width: 140,
      render: (_value, record) => accuracy(record.summary?.twoStage?.fullAccuracy),
    },
    { title: '状态', width: 100, render: (_value, record) => statusTag(record.status) },
    { title: '创建时间', dataIndex: 'createdAt', width: 180, render: formatDate },
    {
      title: '操作',
      width: 90,
      render: (_value, record) => (
        <Button type="link" icon={<EyeOutlined />} onClick={() => setActiveExperiment(record)}>查看</Button>
      ),
    },
  ]

  const directMetric = activeExperiment?.summary?.direct
  const twoStageMetric = activeExperiment?.summary?.twoStage

  return (
    <Space className="app-page" direction="vertical" size={12} style={{ width: '100%' }}>
      <HeaderActions page="review-mode-experiment">
        <Space className="app-header-action-space" size={8} wrap>
          <Button icon={<ReloadOutlined />} loading={loading} onClick={loadAll}>刷新</Button>
          {isActive(activeExperiment?.status) ? (
            <Button danger icon={<PauseCircleOutlined />} loading={cancelling} onClick={stopExperiment}>终止实验</Button>
          ) : null}
        </Space>
      </HeaderActions>

      <Alert
        type="info"
        showIcon
        message="同一案例、两种评审方式、同一套人工基准"
        description="直接评审只读取需求内容和待评审加工逻辑；两阶段评审按现有 Phase 1 + Phase 2 链路执行。命中率来自验证案例中的期望结论与问题级标注，不以任一模型产物作为正确答案。"
      />

      <Card
        className="compact-card"
        title="创建实验"
        extra={(
          <Space>
            <Typography.Text type="secondary">重复执行</Typography.Text>
            <Select
              value={repetitions}
              disabled={isActive(activeExperiment?.status)}
              style={{ width: 100 }}
              options={[1, 2, 3].map((value) => ({ value, label: `${value} 次` }))}
              onChange={setRepetitions}
            />
            <Button
              type="primary"
              icon={<PlayCircleOutlined />}
              loading={submitting}
              disabled={isActive(activeExperiment?.status) || !selectedCaseIds.length}
              onClick={startExperiment}
            >
              开始对比
            </Button>
          </Space>
        )}
      >
        <Table
          rowKey="caseId"
          size="small"
          loading={loading}
          columns={caseColumns}
          dataSource={cases}
          pagination={{ pageSize: 6, showSizeChanger: false }}
          rowSelection={{
            selectedRowKeys: selectedCaseIds,
            preserveSelectedRowKeys: true,
            onChange: setSelectedCaseIds,
          }}
        />
      </Card>

      {activeExperiment ? (
        <>
          <Card
            className="compact-card"
            title={(
              <Space>
                <FileSearchOutlined />
                <span>实验结果</span>
                {statusTag(activeExperiment.status)}
              </Space>
            )}
          >
            <Descriptions size="small" column={{ xs: 1, sm: 2, lg: 4 }}>
              <Descriptions.Item label="实验编号">{activeExperiment.experimentId}</Descriptions.Item>
              <Descriptions.Item label="案例 / 轮次">{activeExperiment.caseCount} / {activeExperiment.repetitions}</Descriptions.Item>
              <Descriptions.Item label="开始时间">{formatDate(activeExperiment.startedAt)}</Descriptions.Item>
              <Descriptions.Item label="完成时间">{formatDate(activeExperiment.finishedAt)}</Descriptions.Item>
            </Descriptions>
            {isActive(activeExperiment.status) ? (
              <Progress
                percent={progress}
                status={activeExperiment.status === 'cancelling' ? 'exception' : 'active'}
                format={() => `${activeExperiment.summary.completed}/${activeExperiment.summary.total}`}
              />
            ) : null}
          </Card>

          <Row gutter={[12, 12]}>
            <Col xs={24} lg={10}>
              <Card className="compact-card">
                <Statistic title="直接评审 · 综合命中率" value={directMetric?.fullAccuracy ?? 0} precision={1} suffix="%" />
                <Typography.Text type="secondary">{directMetric ? metricDescription(directMetric) : '-'}</Typography.Text>
              </Card>
            </Col>
            <Col xs={24} lg={10}>
              <Card className="compact-card">
                <Statistic title="两阶段评审 · 综合命中率" value={twoStageMetric?.fullAccuracy ?? 0} precision={1} suffix="%" />
                <Typography.Text type="secondary">{twoStageMetric ? metricDescription(twoStageMetric) : '-'}</Typography.Text>
              </Card>
            </Col>
            <Col xs={24} lg={4}>
              <Card className="compact-card">
                <Statistic
                  title="当前领先"
                  value={
                    activeExperiment.summary.winner === 'direct'
                      ? '直接评审'
                      : activeExperiment.summary.winner === 'twoStage'
                        ? '两阶段'
                        : '持平'
                  }
                />
                <Typography.Text type="secondary">以综合命中率计算</Typography.Text>
              </Card>
            </Col>
          </Row>

          <Card className="compact-card" title="逐案例结果">
            <Table
              rowKey="itemId"
              size="small"
              columns={resultColumns}
              dataSource={activeExperiment.items}
              pagination={{ pageSize: 10, showSizeChanger: true }}
              scroll={{ x: 1050 }}
              locale={{ emptyText: <Empty description="实验尚未产生结果" /> }}
            />
          </Card>
        </>
      ) : null}

      <Card className="compact-card" title="实验历史">
        <Table
          rowKey="experimentId"
          size="small"
          loading={loading}
          columns={historyColumns}
          dataSource={experiments}
          pagination={{ pageSize: 8, showSizeChanger: false }}
          scroll={{ x: 1050 }}
          locale={{ emptyText: <Empty description="暂无模式对比实验" /> }}
        />
      </Card>

      <Modal
        open={documentOpen}
        title={documentTitle}
        width="min(1080px, 92vw)"
        footer={null}
        destroyOnHidden
        onCancel={() => setDocumentOpen(false)}
      >
        <Spin spinning={documentLoading}>
          <div style={{ minHeight: 180 }}>
            {documentText ? <MarkdownViewer content={documentText} /> : null}
          </div>
        </Spin>
      </Modal>
    </Space>
  )
}
