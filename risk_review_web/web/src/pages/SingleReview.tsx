import {
  Alert,
  Button,
  Card,
  Checkbox,
  Col,
  Form,
  Input,
  Modal,
  Row,
  Select,
  Space,
  Spin,
  Table,
  Tabs,
  Tag,
  Timeline,
  Typography,
  message,
} from 'antd'
import type { ColumnsType } from 'antd/es/table'
import type { CSSProperties } from 'react'
import { useEffect, useMemo, useRef, useState } from 'react'

import { EmptyState } from '../components/EmptyState'
import { MarkdownViewer, isJsonContent, isMarkdownContent } from '../components/MarkdownViewer'
import { ReviewResultPanel } from '../components/ReviewResultPanel'
import {
  cancelTask,
  fetchOutputJson,
  fetchOutputText,
  fetchValidationCases,
  fetchTask,
  runPhase1,
  runPhase2,
} from '../api/reviewApi'
import type {
  ApiDocItem,
  AppNotice,
  ExampleItem,
  Phase1Response,
  Phase1Standard,
  Phase2Response,
  ReviewHistoryRecord,
  ReviewJsonPayload,
  ReviewResult,
  TaskLog,
  TaskRecord,
  ValidationCase,
  ValidationItem,
} from '../types/review'
import { buildDraftFromSections, formatDateTimeValue, parseDraftSections, parseReviewResult, parseStandardFromJson, taskFailureMessage } from '../utils/reviewTransform'
import { StatusBadge } from '../components/StatusBadge'

const RUN_INVALIDATED = 'RUN_INVALIDATED'
const DEFAULT_VALUE_SEMANTICS = '字段缺失、格式错误或数据异常时返回默认值'

interface SingleReviewProps {
  apiDocs: ApiDocItem[]
  examples: ExampleItem[]
  onNotice: (notice: AppNotice) => void
  prefillCase?: ValidationCase | null
  prefillStandardRecord?: ReviewHistoryRecord | null
  onPrefillConsumed?: () => void
  onPrefillStandardConsumed?: () => void
}

interface ReviewFormValues {
  factorName: string
  requirement: string
  apiNames: string[]
  businessRuleIds?: string[]
  paramFields?: string
  procLogic?: string
  defaultValues?: string
}

type CaseOption = ExampleItem | ValidationCase

interface ExecutionLogItem {
  id: string
  time: string
  phase: 'phase1' | 'phase2' | 'system'
  level: 'info' | 'success' | 'warning' | 'error'
  message: string
  detail?: string
  stream?: string
  isDebug?: boolean
}

const surfaceStyle: CSSProperties = {
  borderColor: '#dbe4ed',
  borderRadius: 8,
  boxShadow: '0 1px 2px rgba(16, 30, 54, 0.03), 0 10px 24px rgba(31, 52, 79, 0.035)',
}

function validationTotal(response?: Pick<Phase1Response | Phase2Response, 'validationSummary' | 'validationItems'> | null) {
  return response?.validationSummary?.totalCount ?? response?.validationItems?.length ?? 0
}

function validationSeverityTag(item: ValidationItem) {
  const severity = item.severity || 'warning'
  if (severity === 'error') return <Tag color="red">严重问题</Tag>
  if (severity === 'autoFix') return <Tag color="blue">自动修复</Tag>
  return <Tag color="gold">警告</Tag>
}

function ValidationIssuesButton({ phase1, phase2 }: { phase1?: Phase1Response | null; phase2?: Phase2Response | null }) {
  const [open, setOpen] = useState(false)
  const items = [
    ...(phase1?.validationItems || []).map((item) => ({ ...item, phase: 'Phase 1' })),
    ...(phase2?.validationItems || []).map((item) => ({ ...item, phase: 'Phase 2' })),
  ]
  return (
    <>
      <Button block onClick={() => setOpen(true)}>
        查看校验问题（{items.length}）
      </Button>
      <Modal
        title="校验问题"
        open={open}
        width="min(920px, 90vw)"
        footer={null}
        onCancel={() => setOpen(false)}
      >
        <Space direction="vertical" size={12} style={{ width: '100%' }}>
          <Alert
            showIcon
            type={items.length ? 'warning' : 'success'}
            message={items.length ? '当前配置为非阻断模式，校验问题不会中断流程' : '暂无校验问题'}
            description={items.length ? '请结合校验问题人工确认结果可信度。' : '当前 Phase 1 / Phase 2 未返回校验问题。'}
          />
          {items.length ? (
            <Table
              size="small"
              rowKey={(_, index) => `validation-${index}`}
              pagination={false}
              dataSource={items}
              columns={[
                { title: '阶段', dataIndex: 'phase', width: 90 },
                { title: '级别', width: 100, render: (_, record) => validationSeverityTag(record) },
                { title: '字段', dataIndex: 'field', width: 180, render: (value) => value || '-' },
                { title: '问题', dataIndex: 'message', render: (value) => value || '-' },
              ]}
              scroll={{ x: 720 }}
            />
          ) : (
            <EmptyState title="暂无校验问题。" />
          )}
        </Space>
      </Modal>
    </>
  )
}

function ArtifactDocumentButton(props: {
  fileId?: string
  title: '准出文档' | '评审文档'
  onNotice: (notice: AppNotice) => void
}) {
  const { fileId, title, onNotice } = props
  const [open, setOpen] = useState(false)
  const [loading, setLoading] = useState(false)
  const [content, setContent] = useState('')
  const [error, setError] = useState<string>()
  const isJson = isJsonContent(content) || String(fileId || '').toLowerCase().endsWith('.json')
  const isMarkdown = !isJson && isMarkdownContent(content, fileId || `${title}.md`)

  async function openDocument() {
    setOpen(true)
    setError(undefined)
    if (!fileId) {
      setContent('')
      return
    }
    setLoading(true)
    try {
      const nextContent = await fetchOutputText(fileId)
      setContent(nextContent)
    } catch (caught) {
      const messageText = caught instanceof Error ? caught.message : `读取${title}失败`
      setError(messageText)
      onNotice({ type: 'error', message: messageText })
    } finally {
      setLoading(false)
    }
  }

  return (
    <>
      <Button block onClick={openDocument} disabled={!fileId}>
        查看{title}
      </Button>
      <Modal
        title={title}
        open={open}
        width="min(860px, 90vw)"
        footer={null}
        onCancel={() => setOpen(false)}
      >
        <Spin spinning={loading}>
          {error ? (
            <Alert type="error" showIcon message={`${title}读取失败`} description={error} />
          ) : !fileId ? (
            <EmptyState title={`暂无${title}。`} />
          ) : isMarkdown ? (
            <MarkdownViewer content={content} fileName={fileId} />
          ) : isJson ? (
            <pre className="single-debug-log-pre">
              <code>
                {(() => {
                  try {
                    return JSON.stringify(JSON.parse(content), null, 2)
                  } catch {
                    return content || '暂无内容'
                  }
                })()}
              </code>
            </pre>
          ) : (
            <Typography.Paragraph code style={{ whiteSpace: 'pre-wrap', display: 'block' }}>
              {content || '暂无内容'}
            </Typography.Paragraph>
          )}
        </Spin>
      </Modal>
    </>
  )
}

function ResultArtifactBar(props: {
  phase1?: Phase1Response | null
  phase2?: Phase2Response | null
  onNotice: (notice: AppNotice) => void
}) {
  const { phase1, phase2, onNotice } = props
  const standardFileId = phase1?.standardFileId
  const reviewFileId = phase2?.reviewFileId
  return (
    <Card className="compact-card result-artifact-card" title="评审产物" style={surfaceStyle}>
      <div className="result-artifact-actions">
        <ValidationIssuesButton phase1={phase1} phase2={phase2} />
        <ArtifactDocumentButton fileId={standardFileId} title="准出文档" onNotice={onNotice} />
        <ArtifactDocumentButton fileId={reviewFileId} title="评审文档" onNotice={onNotice} />
      </div>
    </Card>
  )
}

export function SingleReview(props: SingleReviewProps) {
  const { apiDocs, examples, onNotice, prefillCase, prefillStandardRecord, onPrefillConsumed, onPrefillStandardConsumed } = props
  const [form] = Form.useForm<ReviewFormValues>()
  const [loadingPhase1, setLoadingPhase1] = useState(false)
  const [loadingPhase2, setLoadingPhase2] = useState(false)
  const [phase1, setPhase1] = useState<Phase1Response | null>(null)
  const [phase2, setPhase2] = useState<Phase2Response | null>(null)
  const [phase1TaskId, setPhase1TaskId] = useState<string>()
  const [phase2TaskId, setPhase2TaskId] = useState<string>()
  const [standard, setStandard] = useState<Phase1Standard | null>(null)
  const [result, setResult] = useState<ReviewResult | null>(null)
  const [selectedCaseId, setSelectedCaseId] = useState<string>()
  const [currentCase, setCurrentCase] = useState<ExampleItem | ValidationCase | null>(null)
  const [drawerTitle, setDrawerTitle] = useState('原始输出')
  const [drawerContent, setDrawerContent] = useState('')
  const [drawerOpen, setDrawerOpen] = useState(false)
  const [error, setError] = useState<string>()
  const [activeStep, setActiveStep] = useState(0)
  const [oneClickRunning, setOneClickRunning] = useState(false)
  const [oneClickPhase, setOneClickPhase] = useState<'phase1' | 'phase2' | null>(null)
  const [skipReviewer, setSkipReviewer] = useState(true)
  const [skipStageValidation, setSkipStageValidation] = useState(true)
  const [showExecutionLog, setShowExecutionLog] = useState(false)
  const [executionLogOpen, setExecutionLogOpen] = useState(false)
  const [executionLogs, setExecutionLogs] = useState<ExecutionLogItem[]>([])
  const [stoppingExecution, setStoppingExecution] = useState(false)
  const [stoppedPhase, setStoppedPhase] = useState<ExecutionLogItem['phase'] | null>(null)
  const [caseModalOpen, setCaseModalOpen] = useState(false)
  const [caseQuery, setCaseQuery] = useState('')
  const [validationCases, setValidationCases] = useState<ValidationCase[]>([])
  const [caseLoading, setCaseLoading] = useState(false)
  const [clearConfirmOpen, setClearConfirmOpen] = useState(false)
  const runTokenRef = useRef(0)
  const activeTaskIdsRef = useRef<Set<string>>(new Set())

  const draftPreview = Form.useWatch([], form)
  const drawerIsJson = isJsonContent(drawerContent) || drawerTitle.toLowerCase().endsWith('.json')
  const drawerIsMarkdown = !drawerIsJson && isMarkdownContent(drawerContent, drawerTitle)

  const draftSummary = useMemo(() => {
    const paramFields = draftPreview?.paramFields || ''
    const procLogic = draftPreview?.procLogic || ''
    const defaultValues = draftPreview?.defaultValues || ''
    return buildDraftFromSections(paramFields, procLogic, defaultValues)
  }, [draftPreview])

  useEffect(() => {
    if (!prefillCase) return
    applyValidationCase(prefillCase)
    onPrefillConsumed?.()
  }, [prefillCase, onPrefillConsumed])

  useEffect(() => {
    if (!prefillStandardRecord) return
    void applyHistoryStandard(prefillStandardRecord).finally(() => onPrefillStandardConsumed?.())
  }, [prefillStandardRecord, onPrefillStandardConsumed])

  function hasInput() {
    const values = form.getFieldsValue()
    return Boolean(
      values.factorName
      || values.requirement
      || values.apiNames?.length
      || values.paramFields
      || values.procLogic
      || (values.defaultValues && values.defaultValues !== DEFAULT_VALUE_SEMANTICS)
      || phase1
      || phase2
      || standard
      || result
      || error
      || executionLogs.length
    )
  }

  function beginExecution() {
    runTokenRef.current += 1
    setStoppedPhase(null)
    return runTokenRef.current
  }

  function invalidateCurrentRun() {
    runTokenRef.current += 1
  }

  function ensureRunCurrent(runToken: number) {
    if (runTokenRef.current !== runToken) {
      throw new Error(RUN_INVALIDATED)
    }
  }

  function isRunInvalidated(caught: unknown) {
    return caught instanceof Error && caught.message === RUN_INVALIDATED
  }

  function collectActiveTaskIds() {
    return Array.from(new Set([
      ...Array.from(activeTaskIdsRef.current),
      phase1TaskId,
      phase2TaskId,
    ].filter(Boolean) as string[]))
  }

  async function cancelTaskIds(taskIds: string[]) {
    if (!taskIds.length) return
    const results = await Promise.allSettled(taskIds.map((taskId) => cancelTask(taskId)))
    const failed = results.find((item): item is PromiseRejectedResult => item.status === 'rejected')
    if (failed && results.every((item) => item.status === 'rejected')) {
      throw failed.reason
    }
  }

  function trackTaskId(taskId: string) {
    activeTaskIdsRef.current.add(taskId)
  }

  function clearPageState() {
    form.resetFields()
    form.setFieldsValue({
      factorName: '',
      requirement: '',
      apiNames: [],
      businessRuleIds: [],
      paramFields: '',
      procLogic: '',
      defaultValues: DEFAULT_VALUE_SEMANTICS,
    })
    resetExecutionState()
    setLoadingPhase1(false)
    setLoadingPhase2(false)
    setOneClickRunning(false)
    setOneClickPhase(null)
    setStoppingExecution(false)
    setStoppedPhase(null)
    setDrawerTitle('原始输出')
    setDrawerContent('')
    setDrawerOpen(false)
    setExecutionLogOpen(false)
    setShowExecutionLog(false)
    setCurrentCase(null)
    setSelectedCaseId(undefined)
    setCaseModalOpen(false)
    setCaseLoading(false)
    setCaseQuery('')
    setExecutionLogs([])
    activeTaskIdsRef.current.clear()
  }

  function appendExecutionLog(
    phase: ExecutionLogItem['phase'],
    level: ExecutionLogItem['level'],
    messageText: string,
    detail?: string
  ) {
    const now = new Date()
    setExecutionLogs((logs) => [
      ...logs,
      {
        id: `${now.getTime()}-${Math.random().toString(36).slice(2, 8)}`,
        time: now.toLocaleTimeString(),
        phase,
        level,
        message: messageText,
        detail,
      },
    ])
  }

  function appendExecutionLogs(items: ExecutionLogItem[]) {
    if (!items.length) return
    setExecutionLogs((logs) => {
      const existingIds = new Set(logs.map((item) => item.id))
      const nextItems = items.filter((item) => !existingIds.has(item.id))
      return nextItems.length ? [...logs, ...nextItems] : logs
    })
  }

  function taskLogTime(log: TaskLog) {
    return log.time ? new Date(log.time * 1000).toLocaleTimeString() : new Date().toLocaleTimeString()
  }

  function taskLogId(taskId: string, phase: ExecutionLogItem['phase'], source: string, index: number, log: TaskLog) {
    return `${taskId}-${phase}-${source}-${log.time ?? index}-${index}`
  }

  function syncTaskLogs(taskId: string, phase: ExecutionLogItem['phase'], task?: TaskRecord) {
    if (!task) return

    const regularLogs = (task.logs || []).map((log, index) => ({
      id: taskLogId(taskId, phase, 'log', index, log),
      time: taskLogTime(log),
      phase,
      level: 'info' as const,
      message: log.message || '任务状态更新',
    }))

    const debugLogs = (task.debugLogs || []).map((log, index) => ({
      id: taskLogId(taskId, phase, 'debug', index, log),
      time: taskLogTime(log),
      phase,
      level: 'info' as const,
      message: log.stream ? `Claude ${log.stream}` : 'Claude 交互',
      detail: log.message || '',
      stream: log.stream,
      isDebug: true,
    }))

    appendExecutionLogs([...regularLogs, ...debugLogs])
  }

  async function pollTaskWithLogs<T = unknown>(taskId: string, phase: ExecutionLogItem['phase'], runToken: number) {
    const interval = 1200
    const timeoutMs = 10 * 60 * 1000
    const start = Date.now()

    while (Date.now() - start < timeoutMs) {
      ensureRunCurrent(runToken)
      const task = await fetchTask(taskId) as TaskRecord<T>
      ensureRunCurrent(runToken)
      syncTaskLogs(taskId, phase, task)
      if (['done', 'error', 'cancelled'].includes(String(task.status || '').toLowerCase())) {
        activeTaskIdsRef.current.delete(taskId)
        return task
      }
      await new Promise((resolve) => setTimeout(resolve, interval))
    }

    throw new Error('任务轮询超时，请稍后在历史记录中查看结果。')
  }

  function openExecutionProcessIfEnabled() {
    if (showExecutionLog) setExecutionLogOpen(true)
  }

  function resetExecutionState() {
    setPhase1(null)
    setPhase2(null)
    setPhase1TaskId(undefined)
    setPhase2TaskId(undefined)
    setStandard(null)
    setResult(null)
    setError(undefined)
    setActiveStep(0)
    setOneClickPhase(null)
    setStoppedPhase(null)
  }

  function caseDisplayName(item: ExampleItem | ValidationCase) {
    return 'caseName' in item ? item.caseName : item.title || item.id
  }

  function caseId(item: ExampleItem | ValidationCase) {
    return 'caseId' in item ? item.caseId : item.id
  }

  function caseKey(item: ExampleItem | ValidationCase) {
    return 'caseId' in item ? `validation:${item.caseId}` : `example:${item.id}`
  }

  function caseFactorName(item: ExampleItem | ValidationCase) {
    return item.factorName || '-'
  }

  function caseExpectedConclusion(item: ExampleItem | ValidationCase) {
    return 'caseId' in item ? item.expectedConclusion : item.expected
  }

  function caseUpdatedAt(item: ExampleItem | ValidationCase) {
    return item.updatedAt
  }

  function caseReviewContent(item: ExampleItem | ValidationCase) {
    return 'caseId' in item ? item.reviewContent || '' : item.reviewContent || item.procLogic || item.draft || ''
  }

  function caseApiNames(item: ExampleItem | ValidationCase) {
    return 'caseId' in item ? item.apiDocIds || [] : item.apiNames || []
  }

  const caseOptions = useMemo<CaseOption[]>(
    () => [...validationCases, ...examples],
    [validationCases, examples]
  )

  const filteredCaseOptions = useMemo(() => {
    const lower = caseQuery.trim().toLowerCase()
    if (!lower) return caseOptions
    return caseOptions.filter((item) =>
      [caseDisplayName(item), caseFactorName(item), caseId(item)]
        .filter(Boolean)
        .some((value) => String(value).toLowerCase().includes(lower))
    )
  }, [caseOptions, caseQuery])

  const selectedCase = useMemo(
    () => caseOptions.find((item) => caseKey(item) === selectedCaseId),
    [caseOptions, selectedCaseId]
  )

  async function openCaseModal() {
    if (loading || stoppingExecution) return
    setCaseModalOpen(true)
    if (validationCases.length || examples.length) return
    setCaseLoading(true)
    try {
      const cases = await fetchValidationCases()
      setValidationCases(cases)
    } catch (caught) {
      const messageText = caught instanceof Error ? caught.message : '加载验证案例失败'
      onNotice({ type: 'error', message: messageText })
    } finally {
      setCaseLoading(false)
    }
  }

  function applyExample(item: ExampleItem) {
    const parsedDraft = parseDraftSections(item.reviewContent || item.draft || '')
    form.setFieldsValue({
      factorName: item.factorName || '',
      requirement: item.requirement || '',
      apiNames: item.apiNames || [],
      businessRuleIds: [],
      paramFields: item.paramFields || parsedDraft.paramFields,
      procLogic: item.procLogic || parsedDraft.procLogic || item.reviewContent || item.draft || '',
      defaultValues: DEFAULT_VALUE_SEMANTICS,
    })
    resetExecutionState()
    setDrawerTitle('原始输出')
    setDrawerContent('')
    setDrawerOpen(false)
    setCurrentCase(item)
    setSelectedCaseId(caseKey(item))
  }

  function applyValidationCase(item: ValidationCase) {
    const parsedDraft = parseDraftSections(item.reviewContent || '')
    form.setFieldsValue({
      factorName: item.factorName,
      requirement: item.requirement,
      apiNames: item.apiDocIds || [],
      businessRuleIds: item.businessRuleIds || [],
      procLogic: parsedDraft.procLogic || item.reviewContent || '',
      paramFields: parsedDraft.paramFields,
      defaultValues: DEFAULT_VALUE_SEMANTICS,
    })
    resetExecutionState()
    setDrawerTitle('原始输出')
    setDrawerContent('')
    setDrawerOpen(false)
    setCurrentCase(item)
    setSelectedCaseId(caseKey(item))
  }

  async function applyHistoryStandard(record: ReviewHistoryRecord) {
    const standardJsonFile = record.standardArtifacts?.json
    if (!standardJsonFile) {
      onNotice({ type: 'warning', message: '该历史记录没有可复用的标准 JSON。' })
      return
    }
    try {
      const standardJson = await fetchOutputJson<Phase1Standard>(standardJsonFile)
      const parsedStandard = parseStandardFromJson(standardJson)
      const apiNames = parsedStandard?.source?.apiDocs
        ?.map((item) => item.name || '')
        .filter(Boolean) || []
      form.setFieldsValue({
        factorName: parsedStandard?.factorName || record.factorName || '',
        requirement: parsedStandard?.source?.requirementSummary || '',
        apiNames,
        paramFields: '',
        procLogic: '',
        defaultValues: DEFAULT_VALUE_SEMANTICS,
      })
      resetExecutionState()
      setPhase1({
        standardFileId: record.standardArtifacts?.md,
        standardJsonFileId: record.standardArtifacts?.json,
        standardRawFileId: record.standardArtifacts?.raw,
        standardValidationFileId: record.standardArtifacts?.validation,
      })
      setStandard(parsedStandard)
      setActiveStep(2)
      setDrawerTitle('原始输出')
      setDrawerContent('')
      setDrawerOpen(false)
      onNotice({ type: 'success', message: `已复用标准：${record.factorName || standardJsonFile}` })
    } catch (caught) {
      const messageText = caught instanceof Error ? caught.message : '复用标准失败'
      onNotice({ type: 'error', message: messageText })
    }
  }

  function handleLoadCase() {
    if (loading || stoppingExecution) return
    const item = selectedCase
    if (!item) {
      onNotice({ type: 'warning', message: '请先选择验证案例' })
      return
    }
    const applyCase = () => {
      if ('caseId' in item) {
        applyValidationCase(item)
      } else {
        applyExample(item)
      }
      setCaseModalOpen(false)
    }
    if (hasInput()) {
      Modal.confirm({
        title: '加载案例会覆盖当前输入，是否继续？',
        content: '加载验证案例会覆盖当前输入和结果，是否继续？',
        okText: '继续加载',
        cancelText: '取消',
        onOk: applyCase,
      })
      return
    }
    applyCase()
  }

  function handleClear() {
    if (loading || stoppingExecution) return
    setClearConfirmOpen(true)
  }

  function confirmClearPage() {
    if (loading || stoppingExecution) return
    const taskIds = collectActiveTaskIds()
    invalidateCurrentRun()
    clearPageState()
    setClearConfirmOpen(false)
    message.success('已清空当前页面内容')
    if (taskIds.length) {
      void cancelTaskIds(taskIds).catch((caught) => {
        const messageText = caught instanceof Error ? caught.message : '停止后端任务失败'
        onNotice({ type: 'warning', message: '页面已清空，但停止后端任务失败', description: messageText })
      })
    }
  }

  async function handleStopExecution() {
    if (!loading && !stoppingExecution) return

    const taskIds = collectActiveTaskIds()
    const stoppingPhase: ExecutionLogItem['phase'] = loadingPhase2 || oneClickPhase === 'phase2' ? 'phase2' : loadingPhase1 || oneClickPhase === 'phase1' ? 'phase1' : 'system'
    invalidateCurrentRun()
    setStoppingExecution(true)
    setStoppedPhase(stoppingPhase)
    setLoadingPhase1(false)
    setLoadingPhase2(false)
    setOneClickRunning(false)
    setOneClickPhase(null)
    appendExecutionLog('system', 'warning', taskIds.length ? '已请求停止当前执行任务' : '已停止前端等待，任务尚未返回 taskId')

    try {
      if (taskIds.length) {
        await cancelTaskIds(taskIds)
      }
      activeTaskIdsRef.current.clear()
      onNotice({ type: 'warning', message: '已请求停止当前执行' })
    } catch (caught) {
      const messageText = caught instanceof Error ? caught.message : '停止执行失败'
      onNotice({ type: 'error', message: messageText })
      appendExecutionLog('system', 'error', '停止执行失败', messageText)
    } finally {
      setStoppingExecution(false)
    }
  }

  async function handleOpenArtifact(fileId: string) {
    try {
      const content = await fetchOutputText(fileId)
      setDrawerTitle(fileId)
      setDrawerContent(content)
      setDrawerOpen(true)
    } catch (caught) {
      const messageText = caught instanceof Error ? caught.message : '读取文件失败'
      onNotice({ type: 'error', message: messageText })
    }
  }

  function logTaskFailure(phase: ExecutionLogItem['phase'], messageText: string, detail?: string) {
    const normalized = messageText.includes('任务轮询超时') ? '任务轮询超时' : phase === 'phase1' ? 'Phase 1 执行失败' : 'Phase 2 执行失败'
    appendExecutionLog(phase, 'error', normalized, detail || messageText)
  }

  async function executePhase1(values: Pick<ReviewFormValues, 'factorName' | 'requirement' | 'apiNames'>, runToken: number) {
    openExecutionProcessIfEnabled()
    // appendExecutionLog('phase1', 'info', 'Phase 1 任务提交中')
    if (skipReviewer) appendExecutionLog('phase1', 'info', 'Reviewer 已跳过')
    if (skipStageValidation) appendExecutionLog('phase1', 'info', '阶段校验已跳过')
    const response = await runPhase1({
      factorName: values.factorName,
      requirement: values.requirement,
      apiNames: values.apiNames,
      debugClaude: showExecutionLog,
      skipReviewer,
      skipStageValidation,
    })
    if (runTokenRef.current !== runToken) {
      void cancelTask(response.taskId)
      throw new Error(RUN_INVALIDATED)
    }
    trackTaskId(response.taskId)
    setPhase1TaskId(response.taskId)
    syncTaskLogs(response.taskId, 'phase1', response.task)
    appendExecutionLog('phase1', 'success', 'Phase 1 任务提交成功')
    appendExecutionLog('phase1', 'info', 'Phase 1 taskId', response.taskId)
    // appendExecutionLog('phase1', 'info', 'Phase 1 轮询开始')

    const task = await pollTaskWithLogs<Phase1Response>(response.taskId, 'phase1', runToken)
    ensureRunCurrent(runToken)
    if (String(task.status || '').toLowerCase() === 'cancelled') {
      throw new Error('Phase 1 已停止')
    }
    const taskResult = (task.result || {}) as Phase1Response

    if (String(task.status).toLowerCase() !== 'done' || taskResult.success === false || taskResult.blocking) {
      throw new Error(taskFailureMessage(taskResult, 'Phase 1 执行失败'))
    }

    const validationCount = validationTotal(taskResult)
    appendExecutionLog(
      'phase1',
      validationCount ? 'warning' : 'success',
      validationCount ? `生成完成，存在 ${validationCount} 个校验问题` : 'Phase 1 执行完成',
      taskResult.cacheHit ? '命中缓存标准' : undefined
    )
    setPhase1(taskResult)

    const standardJsonFileId = taskResult.standardJsonFileId || taskResult.standardSummaryFileId
    if (standardJsonFileId) {
      try {
        const standardJson = await fetchOutputJson<Phase1Standard>(standardJsonFileId)
        ensureRunCurrent(runToken)
        const parsedStandard = parseStandardFromJson(standardJson)
        setStandard(parsedStandard)
        appendExecutionLog('phase1', 'success', 'Phase 1 标准 JSON 读取完成', standardJsonFileId)
      } catch (caught) {
        const messageText = caught instanceof Error ? caught.message : '读取产物失败'
        appendExecutionLog('phase1', 'error', '读取产物失败', messageText)
        throw caught
      }
    } else {
      appendExecutionLog('phase1', 'warning', 'Phase 1 未返回标准 JSON 文件', '页面将仅展示 Markdown 产物。')
    }

    return taskResult
  }

  async function executePhase2(values: ReviewFormValues, standardFileId: string, draft: string, runToken: number) {
    openExecutionProcessIfEnabled()
    appendExecutionLog('phase2', 'info', 'Phase 2 任务提交中')
    if (skipReviewer) appendExecutionLog('phase2', 'info', 'Reviewer 已跳过')
    if (skipStageValidation) appendExecutionLog('phase2', 'info', '阶段校验已跳过')
    const response = await runPhase2({
      factorName: values.factorName,
      requirement: values.requirement,
      apiNames: values.apiNames,
      draft,
      standardFileId,
      debugClaude: showExecutionLog,
      skipReviewer,
      skipStageValidation,
    })
    if (runTokenRef.current !== runToken) {
      void cancelTask(response.taskId)
      throw new Error(RUN_INVALIDATED)
    }
    trackTaskId(response.taskId)
    setPhase2TaskId(response.taskId)
    syncTaskLogs(response.taskId, 'phase2', response.task)
    appendExecutionLog('phase2', 'success', 'Phase 2 任务提交成功')
    appendExecutionLog('phase2', 'info', 'Phase 2 taskId', response.taskId)
    appendExecutionLog('phase2', 'info', 'Phase 2 轮询开始')

    const task = await pollTaskWithLogs<Phase2Response>(response.taskId, 'phase2', runToken)
    ensureRunCurrent(runToken)
    if (String(task.status || '').toLowerCase() === 'cancelled') {
      throw new Error('Phase 2 已停止')
    }
    const taskResult = (task.result || {}) as Phase2Response

    if (String(task.status).toLowerCase() !== 'done' || taskResult.success === false || taskResult.blocking) {
      throw new Error(taskFailureMessage(taskResult, 'Phase 2 执行失败'))
    }

    const validationCount = validationTotal(taskResult)
    appendExecutionLog(
      'phase2',
      validationCount ? 'warning' : 'success',
      validationCount ? `评审完成，存在 ${validationCount} 个校验问题` : 'Phase 2 执行完成',
      taskResult.degraded ? 'Phase 2 已跳过或降级执行，因为标准 JSON 不可用' : undefined
    )
    setPhase2(taskResult)

    try {
      let reviewMarkdown = taskResult.stdout || ''
      if (taskResult.reviewFileId) {
        try {
          reviewMarkdown = await fetchOutputText(taskResult.reviewFileId)
          ensureRunCurrent(runToken)
          appendExecutionLog('phase2', 'success', 'Phase 2 评审 Markdown 读取完成', taskResult.reviewFileId)
        } catch (caught) {
          const messageText = caught instanceof Error ? caught.message : '读取评审 Markdown 失败'
          appendExecutionLog('phase2', 'warning', '读取评审 Markdown 失败', `${messageText}；页面将使用 Phase 2 stdout。`)
        }
      }

      let reviewJson: ReviewJsonPayload | null = null
      if (taskResult.reviewJsonFileId) {
        try {
          reviewJson = await fetchOutputJson<ReviewJsonPayload>(taskResult.reviewJsonFileId)
          ensureRunCurrent(runToken)
          appendExecutionLog('phase2', 'success', 'Phase 2 review JSON 读取完成', taskResult.reviewJsonFileId)
        } catch (caught) {
          const messageText = caught instanceof Error ? caught.message : '读取 review JSON 失败'
          appendExecutionLog('phase2', 'warning', '读取 review JSON 失败', `${messageText}；不影响评审 Markdown 展示。`)
        }
      } else {
        appendExecutionLog('phase2', 'info', 'Phase 2 未返回 review JSON 文件', '页面直接展示评审 Markdown。')
      }
      ensureRunCurrent(runToken)
      setResult(parseReviewResult(reviewJson, reviewMarkdown))
    } catch (caught) {
      const messageText = caught instanceof Error ? caught.message : '读取产物失败'
      appendExecutionLog('phase2', 'error', '读取产物失败', messageText)
      throw caught
    }

    return taskResult
  }

  async function handlePhase1() {
    const values = await form.validateFields(['factorName', 'requirement', 'apiNames'])
    const runToken = beginExecution()
    setLoadingPhase1(true)
    setError(undefined)
    setPhase1(null)
    setPhase2(null)
    setPhase1TaskId(undefined)
    setPhase2TaskId(undefined)
    setStandard(null)
    setResult(null)
    setActiveStep(1)
    appendExecutionLog('system', 'info', '开始生成准出标准')

    try {
      const taskResult = await executePhase1(values, runToken)
      ensureRunCurrent(runToken)
      setActiveStep(2)
      const count = validationTotal(taskResult)
      onNotice({ type: count ? 'warning' : 'success', message: count ? `生成完成，存在 ${count} 个校验问题` : taskResult.cacheHit ? 'Phase 1 命中缓存' : 'Phase 1 已完成' })
    } catch (caught) {
      if (isRunInvalidated(caught)) return
      const messageText = caught instanceof Error ? caught.message : 'Phase 1 执行失败'
      setError(messageText)
      setActiveStep(1)
      logTaskFailure('phase1', messageText)
      onNotice({ type: 'error', message: messageText })
    } finally {
      if (runTokenRef.current === runToken) {
        setLoadingPhase1(false)
      }
    }
  }

  async function handlePhase2() {
    const values = await form.validateFields()
    if (!phase1?.standardFileId) {
      message.warning('请先完成 Phase 1。')
      return
    }

    const draft = buildDraftFromSections(values.paramFields || '', values.procLogic || '', values.defaultValues || '')
    if (!draft.trim()) {
      setError('请填写待评审准出内容。')
      return
    }

    setLoadingPhase2(true)
    const runToken = beginExecution()
    setError(undefined)
    setPhase2(null)
    setPhase2TaskId(undefined)
    setResult(null)
    setActiveStep(3)
    appendExecutionLog('system', 'info', '开始执行准出评审')

    try {
      const taskResult = await executePhase2(values, phase1.standardFileId, draft, runToken)
      ensureRunCurrent(runToken)
      setActiveStep(4)
      const count = validationTotal(taskResult)
      onNotice({ type: count ? 'warning' : 'success', message: count ? `评审完成，存在 ${count} 个校验问题` : 'Phase 2 已完成' })
    } catch (caught) {
      if (isRunInvalidated(caught)) return
      const messageText = caught instanceof Error ? caught.message : 'Phase 2 执行失败'
      setError(messageText)
      setActiveStep(3)
      logTaskFailure('phase2', messageText)
      onNotice({ type: 'error', message: messageText })
    } finally {
      if (runTokenRef.current === runToken) {
        setLoadingPhase2(false)
      }
    }
  }

  async function handleOneClickReview() {
    const values = await form.validateFields()
    const draft = buildDraftFromSections(values.paramFields || '', values.procLogic || '', values.defaultValues || '')
    if (!draft.trim()) {
      setError('请填写待评审准出内容。')
      appendExecutionLog('system', 'warning', '请填写待评审准出内容。')
      return
    }

    const runToken = beginExecution()
    setOneClickRunning(true)
    setLoadingPhase1(true)
    setOneClickPhase('phase1')
    setError(undefined)
    setPhase1(null)
    setPhase2(null)
    setPhase1TaskId(undefined)
    setPhase2TaskId(undefined)
    setStandard(null)
    setResult(null)
    setActiveStep(1)
    appendExecutionLog('system', 'info', '开始一键评审')

    let runningPhase: ExecutionLogItem['phase'] = 'phase1'
    try {
      const phase1Result = await executePhase1(values, runToken)
      ensureRunCurrent(runToken)
      if (!phase1Result.standardFileId) {
        throw new Error('Phase 1 未返回准出标准文件，无法继续 Phase 2。')
      }
      setActiveStep(2)

      setLoadingPhase1(false)
      setLoadingPhase2(true)
      setOneClickPhase('phase2')
      runningPhase = 'phase2'
      setActiveStep(3)
      const phase2Result = await executePhase2(values, phase1Result.standardFileId, draft, runToken)
      ensureRunCurrent(runToken)
      setActiveStep(4)
      const totalValidationCount = validationTotal(phase1Result) + validationTotal(phase2Result)
      appendExecutionLog(
        'system',
        totalValidationCount ? 'warning' : 'success',
        totalValidationCount ? `一键评审完成，存在 ${totalValidationCount} 个校验问题` : '一键评审完成'
      )
      onNotice({
        type: totalValidationCount ? 'warning' : 'success',
        message: totalValidationCount ? `一键评审完成，存在 ${totalValidationCount} 个校验问题` : '一键评审已完成',
      })
    } catch (caught) {
      if (isRunInvalidated(caught)) return
      const messageText = caught instanceof Error ? caught.message : '一键评审失败'
      setError(messageText)
      setActiveStep(runningPhase === 'phase2' ? 3 : 1)
      logTaskFailure(runningPhase, messageText)
      onNotice({ type: 'error', message: messageText })
    } finally {
      if (runTokenRef.current === runToken) {
        setLoadingPhase1(false)
        setLoadingPhase2(false)
        setOneClickRunning(false)
        setOneClickPhase(null)
      }
    }
  }

  const loading = loadingPhase1 || loadingPhase2 || oneClickRunning
  const executionDisabled = loading || stoppingExecution
  const oneClickButtonText = oneClickRunning
    ? oneClickPhase === 'phase2'
      ? '准出评审中...'
      : oneClickPhase === 'phase1'
        ? '生成标准中...'
        : '一键评审中...'
    : '一键评审'
  const flowStepTitles = ['填写信息', '生成标准', '标准完成', '执行评审', '评审完成']
  const phase1ValidationCount = validationTotal(phase1)
  const phase2ValidationCount = validationTotal(phase2)
  const phase1StatusText = stoppedPhase === 'phase1'
    ? '已停止'
    : phase1?.standardFileId
      ? phase1ValidationCount ? `生成完成，存在 ${phase1ValidationCount} 个校验问题` : '已完成'
      : loadingPhase1 ? '运行中' : '待执行'
  const phase2StatusText = stoppedPhase === 'phase2'
    ? '已停止'
    : phase2?.reviewFileId
      ? phase2ValidationCount ? `评审完成，存在 ${phase2ValidationCount} 个校验问题` : '已完成'
      : loadingPhase2 ? '运行中' : phase1?.standardFileId ? '待执行' : '等待标准'
  const currentTaskPhaseText = stoppedPhase
    ? '已停止'
    : oneClickRunning
      ? oneClickPhase === 'phase2' ? '一键评审 / Phase 2' : '一键评审 / Phase 1'
      : loadingPhase2 ? 'Phase 2'
        : loadingPhase1 ? 'Phase 1'
            : phase2?.reviewFileId ? '评审完成'
              : phase1?.standardFileId ? '标准已生成'
              : '未开始'
  const phase1Tone = stoppedPhase === 'phase1' ? 'warning' : error && activeStep <= 1 ? 'error' : loadingPhase1 ? 'processing' : phase1?.standardFileId ? 'success' : 'default'
  const phase2Tone = stoppedPhase === 'phase2' ? 'warning' : error && activeStep >= 3 ? 'error' : loadingPhase2 ? 'processing' : phase2?.reviewFileId ? 'success' : phase1?.standardFileId ? 'warning' : 'default'
  const statusTagStyle: Record<string, CSSProperties> = {
    success: {
      background: '#edf9f3',
      borderColor: '#b9e6cf',
      color: '#176b4d',
    },
    processing: {},
    warning: {},
    error: {},
    default: {},
  }
  const logTagColor: Record<ExecutionLogItem['level'], string> = {
    info: 'blue',
    success: 'green',
    warning: 'orange',
    error: 'red',
  }
  const logPhaseText: Record<ExecutionLogItem['phase'], string> = {
    phase1: 'Phase 1',
    phase2: 'Phase 2',
    system: 'System',
  }
  const timelineLogs = [...executionLogs].slice(-100).reverse()
  const simpleTimelineLogs = timelineLogs.filter((item) => !item.isDebug)
  const debugTimelineLogs = timelineLogs.filter((item) => item.isDebug)
  const debugLogCount = executionLogs.filter((item) => item.isDebug).length
  const renderExecutionTimeline = (items: ExecutionLogItem[]) => (
    items.length ? (
      <Timeline
        items={items.map((item) => ({
          color: item.level === 'error' ? 'red' : item.level === 'warning' ? 'orange' : item.level === 'success' ? 'green' : 'blue',
          children: (
            <Space direction="vertical" size={4} style={{ width: '100%' }}>
              <Space size={6} wrap>
                <Typography.Text type="secondary">{item.time}</Typography.Text>
                <Tag color={logTagColor[item.level]}>{logPhaseText[item.phase]}</Tag>
                {item.stream ? <Tag>{item.stream}</Tag> : null}
              </Space>
              <Typography.Text>{item.message}</Typography.Text>
              {item.detail ? (
                <pre className={item.isDebug ? 'single-debug-log-pre' : 'single-log-detail-pre'}>
                  {item.detail}
                </pre>
              ) : null}
            </Space>
          ),
        }))}
      />
    ) : (
      <EmptyState title="暂无执行日志。" />
    )
  )
  const caseColumns: ColumnsType<CaseOption> = [
    { title: '案例名称', width: 220, render: (_: unknown, record: CaseOption) => caseDisplayName(record) },
    { title: '因子名称', width: 180, render: (_: unknown, record: CaseOption) => caseFactorName(record) },
    { title: '期望结论', width: 110, render: (_: unknown, record: CaseOption) => caseExpectedConclusion(record) || '-' },
    {
      title: '最近执行结果',
      width: 130,
      render: (_: unknown, record: CaseOption) => (
        'lastActualConclusion' in record && record.lastActualConclusion ? <StatusBadge value={record.lastActualConclusion} /> : <Typography.Text type="secondary">-</Typography.Text>
      ),
    },
    { title: '更新时间', width: 180, render: (_: unknown, record: CaseOption) => formatDateTimeValue(caseUpdatedAt(record)) },
  ]

  return (
    <Space className="app-page compact-form single-review-page" direction="vertical" size={12} style={{ width: '100%' }}>
      <div className="single-review-steps" aria-label="评审流程进度">
        {flowStepTitles.map((title, index) => {
          const isActive = index === activeStep
          const isDone = index < activeStep
          return (
            <div
              key={title}
              className={isActive ? 'single-step-wrap is-active' : isDone ? 'single-step-wrap is-done' : 'single-step-wrap'}
            >
              <div className={isActive ? 'single-step-pill is-active' : isDone ? 'single-step-pill is-done' : 'single-step-pill'}>
                <span className="single-step-index">{isDone ? '✓' : index + 1}</span>
                <span className="single-step-title">{title}</span>
              </div>
              {index < flowStepTitles.length - 1 ? <span className="single-step-connector" aria-hidden="true" /> : null}
            </div>
          )
        })}
      </div>
      <div className="single-review-step-compact">
        步骤 {activeStep + 1} / {flowStepTitles.length}：{flowStepTitles[activeStep] || flowStepTitles[0]}
      </div>
      <Row className="single-review-workbench-row" gutter={[16, 12]} align="stretch">
        <Col xs={24} xl={16} className="single-main-col">
	          <Card
	            className="compact-card single-input-card"
	            title="人工输入"
	            extra={
	              <Space size={8} wrap>
	                <Button size="small" disabled={executionDisabled} onClick={openCaseModal}>加载验证案例</Button>
	                <Button size="small" danger disabled={executionDisabled} onClick={handleClear}>清空</Button>
	              </Space>
	            }
	            style={surfaceStyle}
	          >
            <Spin spinning={loading}>
	              <Form form={form} layout="vertical" initialValues={{ apiNames: [], defaultValues: DEFAULT_VALUE_SEMANTICS }} className="single-review-form">
	                <Row gutter={[12, 0]}>
	                  <Col xs={24}>
	                    <Form.Item name="factorName" label="因子名称" rules={[{ required: true, message: '请输入因子名称' }]}>
	                      <Input placeholder="例如：近30日交易金额" />
	                    </Form.Item>
	                  </Col>
	                  <Col xs={24}>
	                    <Form.Item name="apiNames" label="关联接口文档" rules={[{ required: true, message: '至少选择一个接口文档' }]}>
	                      <Select
	                        mode="multiple"
	                        placeholder="选择接口文档"
	                        options={apiDocs.map((item) => ({
	                          label: `${item.name}${item.category ? ` · ${item.category}` : ''}`,
	                          value: item.name,
	                        }))}
	                      />
	                    </Form.Item>
	                  </Col>
	                  <Col xs={24}>
	                    <Form.Item name="requirement" label="需求内容" rules={[{ required: true, message: '请输入需求内容' }]}>
	                      <Input.TextArea rows={3} placeholder="填写业务背景、使用场景和加工逻辑要求" />
	                    </Form.Item>
	                  </Col>
	                  <Col xs={24}>
                      <div className="review-content-fieldset">
                        <div className="review-content-fieldset-title">待评审内容</div>
                        <Row gutter={[12, 0]}>
                          <Col xs={24} md={12}>
                            <Form.Item name="paramFields" label="因子加工输入字段">
                              <Input.TextArea rows={3} placeholder="每行一个字段，建议格式：字段名 | 字段来源 | 用途" />
                            </Form.Item>
                          </Col>
                          <Col xs={24} md={12}>
                            <Form.Item name="defaultValues" label="默认值">
                              <Input.TextArea rows={3} readOnly />
                            </Form.Item>
                          </Col>
                          <Col xs={24}>
                            <Form.Item name="procLogic" label="加工逻辑" rules={[{ required: true, message: '请输入加工逻辑' }]}>
                              <Input.TextArea rows={4} placeholder="逐步填写准出加工逻辑" />
                            </Form.Item>
                          </Col>
                        </Row>
                      </div>
	                  </Col>
	                </Row>
		                <div className="single-action-panel">
	                  <Space direction="vertical" size={8} style={{ width: '100%' }}>
	                    <Typography.Text className="single-action-copy">
	                      <strong>执行操作</strong>
	                      <span>主路径优先一键串联，分步操作用于调试和复用标准。</span>
	                    </Typography.Text>
	                    <Space size={12} wrap>
	                      <Checkbox
	                        checked={skipReviewer}
	                        disabled={executionDisabled}
	                        onChange={(event) => setSkipReviewer(event.target.checked)}
	                      >
	                        跳过 Reviewer
	                      </Checkbox>
	                      <Checkbox
	                        checked={skipStageValidation}
	                        disabled={executionDisabled}
	                        onChange={(event) => setSkipStageValidation(event.target.checked)}
	                      >
	                        跳过阶段校验
	                      </Checkbox>
	                      <Checkbox
	                        checked={showExecutionLog}
	                        disabled={executionDisabled}
	                        onChange={(event) => setShowExecutionLog(event.target.checked)}
	                      >
	                        展示执行日志
	                      </Checkbox>
	                    </Space>
	                    <div className="single-action-controls">
	                    <Button
	                      block
	                      size="middle"
	                      type="primary"
	                      loading={oneClickRunning}
	                      disabled={executionDisabled && !oneClickRunning}
                      onClick={handleOneClickReview}
	                    >
	                      {oneClickButtonText}
	                    </Button>
	                      <Select
                          className="single-step-select"
	                        size="small"
	                        placeholder="分步操作"
                        value={undefined}
                        options={[
                          { label: '仅生成标准', value: 'phase1', disabled: executionDisabled },
                          { label: '执行准出评审', value: 'phase2', disabled: executionDisabled || !phase1?.standardFileId },
                        ]}
                        onSelect={(value) => {
                          if (value === 'phase1') void handlePhase1()
	                          if (value === 'phase2') void handlePhase2()
	                        }}
	                      />
	                    </div>
                  </Space>
                </div>
              </Form>
            </Spin>
          </Card>
        </Col>

        <Col xs={24} xl={8} className="single-side-col">
          <div className="single-side-stack">
            <Card className="compact-card single-status-card" title="执行状态" style={surfaceStyle}>
              <Space direction="vertical" size={8} style={{ width: '100%' }}>
                <div className="single-status-row">
                  <span>Phase 1</span>
                  <Tag color={phase1Tone === 'success' ? undefined : phase1Tone} style={statusTagStyle[phase1Tone]}>{phase1StatusText}</Tag>
                </div>
                <div className="single-status-row">
                  <span>Phase 2</span>
                  <Tag color={phase2Tone === 'success' ? undefined : phase2Tone} style={statusTagStyle[phase2Tone]}>{phase2StatusText}</Tag>
                </div>
                <div className="single-status-row">
                  <span>当前阶段</span>
                  <Typography.Text strong ellipsis>{currentTaskPhaseText}</Typography.Text>
                </div>
                <Button
                  block
                  disabled={!executionLogs.length}
                  onClick={() => setExecutionLogOpen(true)}
                >
                  查看执行日志（{executionLogs.length}）
                </Button>
                <Button
                  block
                  danger
                  disabled={!loading || stoppingExecution}
                  loading={stoppingExecution}
                  onClick={handleStopExecution}
                >
                  停止执行
                </Button>
                {error ? <Alert type="error" showIcon message="执行失败" description={error} /> : null}
                {stoppedPhase ? (
                  <Typography.Text type="warning" style={{ fontSize: 12 }}>执行已停止，可调整输入后重新发起评审。</Typography.Text>
                ) : !error && !loading ? (
                  <Typography.Text type="secondary" style={{ fontSize: 12 }}>等待状态会保留当前输入，可继续编辑后重新执行。</Typography.Text>
                ) : null}
              </Space>
            </Card>
            <ResultArtifactBar
              phase1={phase1}
              phase2={phase2}
              onNotice={onNotice}
            />
          </div>
        </Col>
      </Row>

      <Card className="compact-card result-panel" title="评审结果" style={surfaceStyle}>
        {loadingPhase2 ? (
          <Spin />
        ) : result ? (
          <ReviewResultPanel
            result={result}
            standard={standard}
            phase1={phase1}
            phase2={phase2}
            onOpenArtifact={handleOpenArtifact}
          />
        ) : (
          <EmptyState title="Phase 2 完成后在这里展示评审结果文档。" />
        )}
      </Card>

      <Modal
        title={drawerTitle}
        open={drawerOpen}
        width="min(860px, 90vw)"
        footer={null}
        onCancel={() => setDrawerOpen(false)}
      >
        {drawerIsMarkdown ? (
          <MarkdownViewer content={drawerContent} fileName={drawerTitle} />
        ) : drawerIsJson ? (
          <pre
            style={{
              margin: 0,
              maxHeight: 'calc(100vh - 220px)',
              overflow: 'auto',
              padding: 16,
              borderRadius: 8,
              background: '#0f172a',
              color: '#e5e7eb',
              whiteSpace: 'pre-wrap',
              wordBreak: 'break-word',
              fontSize: 13,
              lineHeight: 1.65,
            }}
          >
            <code>
              {(() => {
                try {
                  return JSON.stringify(JSON.parse(drawerContent), null, 2)
                } catch {
                  return drawerContent || '暂无内容'
                }
              })()}
            </code>
          </pre>
        ) : (
          <Typography.Paragraph code style={{ whiteSpace: 'pre-wrap', display: 'block' }}>
            {drawerContent || '暂无内容'}
          </Typography.Paragraph>
        )}
      </Modal>

      <Modal
        title="执行日志"
        open={executionLogOpen}
        width="min(960px, 92vw)"
        footer={null}
        onCancel={() => setExecutionLogOpen(false)}
      >
        <Tabs
          size="small"
          items={[
            {
              key: 'runtime',
              label: `运行日志（${simpleTimelineLogs.length}）`,
              children: (
                <div className="execution-log-modal-body">
                  {renderExecutionTimeline(simpleTimelineLogs)}
                </div>
              ),
            },
            {
              key: 'debug',
              label: `调试输出（${debugLogCount}）`,
              children: (
                <div className="execution-log-modal-body">
                  {renderExecutionTimeline(debugTimelineLogs)}
                </div>
              ),
            },
          ]}
        />
      </Modal>

      <Modal
        title="加载验证案例"
        open={caseModalOpen}
        width="min(920px, 90vw)"
        onCancel={() => setCaseModalOpen(false)}
        onOk={handleLoadCase}
        okText="确认加载"
        okButtonProps={{ disabled: !selectedCase || executionDisabled }}
      >
        <Space direction="vertical" size={12} style={{ width: '100%' }}>
          <Input.Search
            allowClear
            disabled={executionDisabled}
            placeholder="按案例名称 / 因子名称搜索"
            value={caseQuery}
            onChange={(event) => setCaseQuery(event.target.value)}
          />
          <Table
            rowKey={(record) => caseKey(record)}
            className="compact-table"
            size="small"
            loading={caseLoading}
            dataSource={filteredCaseOptions}
            columns={caseColumns}
            pagination={{ pageSize: 6, size: 'small' }}
            rowSelection={{
              type: 'radio',
              selectedRowKeys: selectedCaseId ? [selectedCaseId] : [],
              getCheckboxProps: () => ({ disabled: executionDisabled }),
              onChange: (keys) => {
                if (!executionDisabled) setSelectedCaseId(String(keys[0] || ''))
              },
            }}
            onRow={(record) => ({
              onClick: () => {
                if (!executionDisabled) setSelectedCaseId(caseKey(record))
              },
            })}
            scroll={{ x: 820 }}
          />
          {!selectedCase ? (
            <EmptyState title="请选择一个验证案例。" />
          ) : null}
        </Space>
      </Modal>

      <Modal
        title="确认清空当前页面内容？"
        open={clearConfirmOpen}
        onCancel={() => setClearConfirmOpen(false)}
        onOk={confirmClearPage}
        okText="清空"
        cancelText="取消"
        okButtonProps={{ danger: true, disabled: executionDisabled }}
      >
        <Typography.Paragraph>
          清空会重置输入、评审结果和执行状态，不会删除历史产物。
        </Typography.Paragraph>
      </Modal>

    </Space>
  )
}
