import {
  Alert,
  Button,
  Card,
  Checkbox,
  Col,
  Drawer,
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
import { usePageActive } from '../components/AppLayout'
import { MarkdownViewer, isJsonContent, isMarkdownContent } from '../components/MarkdownViewer'
import { ReviewResultPanel } from '../components/ReviewResultPanel'
import {
  cancelTask,
  fetchApiDocDetail,
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
  Phase2Response,
  ReviewHistoryRecord,
  ReviewResult,
  StandardMetadataV5,
  TaskLog,
  TaskRecord,
  ValidationCase,
} from '../types/review'
import { buildDraftFromSections, formatDateTimeValue, parseDraftSections, parseReviewResult, taskFailureMessage } from '../utils/reviewTransform'
import { StatusBadge } from '../components/StatusBadge'

const RUN_INVALIDATED = 'RUN_INVALIDATED'
const DEFAULT_VALUE_SEMANTICS = '字段缺失、格式错误或数据异常时返回默认值'
const SINGLE_DRAFT_KEY = 'risk-review.single-draft.v1'

interface SingleReviewProps {
  apiDocs: ApiDocItem[]
  examples: ExampleItem[]
  onNotice: (notice: AppNotice) => void
  prefillStandardRecord?: ReviewHistoryRecord | null
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

function ArtifactDocumentButton(props: {
  fileId?: string
  title: '标准加工规范' | '评审文档'
  onNotice: (notice: AppNotice) => void
}) {
  const pageActive = usePageActive()
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
      <Button onClick={openDocument} disabled={!fileId}>
        查看{title}
      </Button>
      <Drawer
        title={title}
        open={pageActive && open}
        width="min(1080px, 82vw)"
        onClose={() => setOpen(false)}
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
      </Drawer>
    </>
  )
}

export function SingleReview(props: SingleReviewProps) {
  const pageActive = usePageActive()
  const { apiDocs, examples, onNotice, prefillStandardRecord, onPrefillStandardConsumed } = props
  const [form] = Form.useForm<ReviewFormValues>()
  const [loadingPhase1, setLoadingPhase1] = useState(false)
  const [loadingPhase2, setLoadingPhase2] = useState(false)
  const [phase1, setPhase1] = useState<Phase1Response | null>(null)
  const [phase2, setPhase2] = useState<Phase2Response | null>(null)
  const [phase1TaskId, setPhase1TaskId] = useState<string>()
  const [phase2TaskId, setPhase2TaskId] = useState<string>()
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
  const [executionLogOpen, setExecutionLogOpen] = useState(false)
  const [executionLogs, setExecutionLogs] = useState<ExecutionLogItem[]>([])
  const [stoppingExecution, setStoppingExecution] = useState(false)
  const [stoppedPhase, setStoppedPhase] = useState<ExecutionLogItem['phase'] | null>(null)
  const [caseModalOpen, setCaseModalOpen] = useState(false)
  const [caseQuery, setCaseQuery] = useState('')
  const [validationCases, setValidationCases] = useState<ValidationCase[]>([])
  const [caseLoading, setCaseLoading] = useState(false)
  const [clearConfirmOpen, setClearConfirmOpen] = useState(false)
  const [apiDocOpen, setApiDocOpen] = useState(false)
  const [apiDocLoading, setApiDocLoading] = useState(false)
  const [apiDocDetail, setApiDocDetail] = useState<ApiDocItem | null>(null)
  const runTokenRef = useRef(0)
  const activeTaskIdsRef = useRef<Set<string>>(new Set())
  const draftRestoredRef = useRef(false)

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
    if (draftRestoredRef.current || prefillStandardRecord) return
    draftRestoredRef.current = true
    try {
      const saved = window.localStorage.getItem(SINGLE_DRAFT_KEY)
      if (!saved) return
      const values = JSON.parse(saved) as Partial<ReviewFormValues>
      form.setFieldsValue({
        ...values,
        defaultValues: values.defaultValues || DEFAULT_VALUE_SEMANTICS,
      })
      onNotice({ type: 'info', message: '已恢复上次未提交内容' })
    } catch {
      window.localStorage.removeItem(SINGLE_DRAFT_KEY)
    }
  }, [form, onNotice, prefillStandardRecord])

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

  function beginNewReviewExecution() {
    setExecutionLogs([])
    setExecutionLogOpen(false)
    activeTaskIdsRef.current.clear()
    return beginExecution()
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
    setCurrentCase(null)
    setSelectedCaseId(undefined)
    setCaseModalOpen(false)
    setCaseLoading(false)
    setCaseQuery('')
    setExecutionLogs([])
    activeTaskIdsRef.current.clear()
    window.localStorage.removeItem(SINGLE_DRAFT_KEY)
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

    const debugLogs = (task.debugLogs || [])
      .filter((log) => String(log.stream || '').toLowerCase() !== 'stderr' || Boolean(log.message))
      .map((log, index) => {
        const stream = String(log.stream || '').toLowerCase()
        const labels: Record<string, string> = {
          prompt: 'Claude 实际输入',
          stdout: 'Claude 原始输出',
          stderr: 'Claude 错误输出',
        }
        return {
          id: taskLogId(taskId, phase, 'debug', index, log),
          time: taskLogTime(log),
          phase,
          level: (stream === 'stderr' && log.message ? 'error' : 'info') as ExecutionLogItem['level'],
          message: labels[stream] || 'Claude 交互',
          detail: log.message || '',
          stream,
          isDebug: true,
        }
      })

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

  function resetExecutionState() {
    setPhase1(null)
    setPhase2(null)
    setPhase1TaskId(undefined)
    setPhase2TaskId(undefined)
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
    const standardMetadataFile = record.standardArtifacts?.json
    const standardFile = record.standardArtifacts?.md
    if (!standardMetadataFile || !standardFile) {
      onNotice({ type: 'warning', message: '该历史记录没有可复用的 v5 标准及元数据。' })
      return
    }
    try {
      const metadata = await fetchOutputJson<StandardMetadataV5>(standardMetadataFile)
      if (metadata.schema_version !== 'risk_review_standard_metadata_v5') {
        throw new Error('该历史标准不是当前版本，请重新生成标准。')
      }
      form.setFieldsValue({
        factorName: metadata.display_metadata?.factor_name || record.factorName || '',
        requirement: metadata.source_inputs?.requirement || '',
        apiNames: metadata.source_inputs?.api_docs || [],
        paramFields: '',
        procLogic: '',
        defaultValues: DEFAULT_VALUE_SEMANTICS,
      })
      resetExecutionState()
      setPhase1({
        standardFileId: standardFile,
        standardMetadataFileId: standardMetadataFile,
      })
      setActiveStep(2)
      setDrawerTitle('原始输出')
      setDrawerContent('')
      setDrawerOpen(false)
      onNotice({ type: 'success', message: `已复用标准：${record.factorName || standardFile}` })
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

  function logTaskFailure(phase: ExecutionLogItem['phase'], messageText: string, detail?: string) {
    const normalized = messageText.includes('任务轮询超时') ? '任务轮询超时' : phase === 'phase1' ? '生成标准失败' : '执行评审失败'
    appendExecutionLog(phase, 'error', normalized, detail || messageText)
  }

  async function executePhase1(values: Pick<ReviewFormValues, 'factorName' | 'requirement' | 'apiNames'>, runToken: number) {
    // appendExecutionLog('phase1', 'info', 'Phase 1 任务提交中')
    const response = await runPhase1({
      factorName: values.factorName,
      requirement: values.requirement,
      apiNames: values.apiNames,
      debugClaude: true,
    })
    if (runTokenRef.current !== runToken) {
      void cancelTask(response.taskId)
      throw new Error(RUN_INVALIDATED)
    }
    trackTaskId(response.taskId)
    setPhase1TaskId(response.taskId)
    syncTaskLogs(response.taskId, 'phase1', response.task)
    appendExecutionLog('phase1', 'success', '生成标准任务提交成功')
    appendExecutionLog('phase1', 'info', '生成标准任务编号', response.taskId)
    // appendExecutionLog('phase1', 'info', 'Phase 1 轮询开始')

    const task = await pollTaskWithLogs<Phase1Response>(response.taskId, 'phase1', runToken)
    ensureRunCurrent(runToken)
    if (String(task.status || '').toLowerCase() === 'cancelled') {
      throw new Error('生成标准已停止')
    }
    const taskResult = (task.result || {}) as Phase1Response

    if (String(task.status).toLowerCase() !== 'done' || taskResult.success === false) {
      throw new Error(taskFailureMessage(taskResult, '生成标准失败'))
    }

    appendExecutionLog(
      'phase1',
      'success',
      '标准生成完成',
      taskResult.cacheHit ? '命中缓存标准' : undefined
    )
    setPhase1(taskResult)

    return taskResult
  }

  async function executePhase2(values: ReviewFormValues, standardFileId: string, draft: string, runToken: number) {
    appendExecutionLog('phase2', 'info', '评审任务提交中')
    const response = await runPhase2({
      factorName: values.factorName,
      requirement: values.requirement,
      draft,
      standardFileId,
      debugClaude: true,
    })
    if (runTokenRef.current !== runToken) {
      void cancelTask(response.taskId)
      throw new Error(RUN_INVALIDATED)
    }
    trackTaskId(response.taskId)
    setPhase2TaskId(response.taskId)
    syncTaskLogs(response.taskId, 'phase2', response.task)
    appendExecutionLog('phase2', 'success', '评审任务提交成功')
    appendExecutionLog('phase2', 'info', '评审任务编号', response.taskId)
    appendExecutionLog('phase2', 'info', '开始等待评审结果')

    const task = await pollTaskWithLogs<Phase2Response>(response.taskId, 'phase2', runToken)
    ensureRunCurrent(runToken)
    if (String(task.status || '').toLowerCase() === 'cancelled') {
      throw new Error('评审已停止')
    }
    const taskResult = (task.result || {}) as Phase2Response

    if (String(task.status).toLowerCase() !== 'done' || taskResult.success === false) {
      throw new Error(taskFailureMessage(taskResult, '执行评审失败'))
    }

    appendExecutionLog(
      'phase2',
      'success',
      '执行评审完成'
    )
    setPhase2(taskResult)

    try {
      let reviewMarkdown = taskResult.stdout || ''
      if (taskResult.reviewFileId) {
        try {
          reviewMarkdown = await fetchOutputText(taskResult.reviewFileId)
          ensureRunCurrent(runToken)
          appendExecutionLog('phase2', 'success', '评审报告读取完成', taskResult.reviewFileId)
        } catch (caught) {
          const messageText = caught instanceof Error ? caught.message : '读取评审 Markdown 失败'
          appendExecutionLog('phase2', 'warning', '读取评审报告失败', `${messageText}；页面将使用任务原始输出。`)
        }
      }

      ensureRunCurrent(runToken)
      setResult(parseReviewResult(reviewMarkdown))
    } catch (caught) {
      const messageText = caught instanceof Error ? caught.message : '读取产物失败'
      appendExecutionLog('phase2', 'error', '读取产物失败', messageText)
      throw caught
    }

    return taskResult
  }

  async function handlePhase1() {
    const values = await form.validateFields(['factorName', 'requirement', 'apiNames'])
    const runToken = beginNewReviewExecution()
    setLoadingPhase1(true)
    setError(undefined)
    setPhase1(null)
    setPhase2(null)
    setPhase1TaskId(undefined)
    setPhase2TaskId(undefined)
    setResult(null)
    setActiveStep(1)
    appendExecutionLog('system', 'info', '开始生成标准加工规范')

    try {
      const taskResult = await executePhase1(values, runToken)
      ensureRunCurrent(runToken)
      setActiveStep(2)
      onNotice({ type: 'success', message: taskResult.cacheHit ? 'Phase 1 命中缓存' : 'Phase 1 已完成' })
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
      setError('请填写待评审加工逻辑。')
      return
    }

    // Phase 2 属于同一轮评审，继续追加日志，保留 Phase 1 的真实输入输出。
    setLoadingPhase2(true)
    const runToken = beginExecution()
    setError(undefined)
    setPhase2(null)
    setPhase2TaskId(undefined)
    setResult(null)
    setActiveStep(3)
    appendExecutionLog('system', 'info', '开始执行加工逻辑评审')

    try {
      const taskResult = await executePhase2(values, phase1.standardFileId, draft, runToken)
      ensureRunCurrent(runToken)
      setActiveStep(4)
      onNotice({ type: 'success', message: 'Phase 2 已完成' })
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
      setError('请填写待评审加工逻辑。')
      appendExecutionLog('system', 'warning', '请填写待评审加工逻辑。')
      return
    }

    const runToken = beginNewReviewExecution()
    setOneClickRunning(true)
    setLoadingPhase1(true)
    setOneClickPhase('phase1')
    setError(undefined)
    setPhase1(null)
    setPhase2(null)
    setPhase1TaskId(undefined)
    setPhase2TaskId(undefined)
    setResult(null)
    setActiveStep(1)
    appendExecutionLog('system', 'info', '开始一键评审')

    let runningPhase: ExecutionLogItem['phase'] = 'phase1'
    try {
      const phase1Result = await executePhase1(values, runToken)
      ensureRunCurrent(runToken)
      if (!phase1Result.standardFileId) {
        throw new Error('未生成标准加工规范，无法继续执行评审。')
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
      appendExecutionLog(
        'system',
        'success',
        '一键评审完成'
      )
      onNotice({
        type: 'success',
        message: '一键评审已完成',
      })
      try {
        window.localStorage.removeItem(SINGLE_DRAFT_KEY)
      } catch {
        // 评审结果已完成，本地存储清理失败不影响结果。
      }
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
      ? '加工逻辑评审中...'
      : oneClickPhase === 'phase1'
        ? '生成标准中...'
        : '一键评审中...'
    : '一键评审'
  const flowStepTitles = ['填写信息', '生成标准', '标准完成', '执行评审', '评审完成']
  const phase1StatusText = stoppedPhase === 'phase1'
    ? '已停止'
    : phase1?.standardFileId
      ? '已完成'
      : loadingPhase1 ? '运行中' : '待执行'
  const phase2StatusText = stoppedPhase === 'phase2'
    ? '已停止'
    : phase2?.reviewFileId
      ? '已完成'
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
    phase1: '生成标准',
    phase2: '执行评审',
    system: '系统',
  }
  const timelineLogs = [...executionLogs].reverse()
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
  const userStatus = stoppedPhase
    ? '已取消'
    : error
      ? '执行失败'
      : loading
        ? '评审中'
        : result
          ? '已完成'
          : '待提交'
  const userStatusColor = error
    ? 'error'
    : loading
      ? 'processing'
      : result
        ? 'success'
        : stoppedPhase
          ? 'default'
          : 'default'

  return (
    <Space className="app-page compact-form single-review-page" direction="vertical" size={12} style={{ width: '100%' }}>
      <Row className="single-review-split" gutter={[0, 16]} align="stretch">
        <Col span={24} className="single-input-pane">
          <section className="work-surface single-input-surface" aria-label="评审输入">
            <div className="surface-heading">
              <div>
                <Typography.Title level={5}>评审输入</Typography.Title>
                <Typography.Text type="secondary">填写需求、接口信息和待评审内容。</Typography.Text>
              </div>
              <Space size={8}>
                <Button size="small" disabled={executionDisabled} onClick={openCaseModal}>加载案例</Button>
                <Button size="small" danger disabled={executionDisabled} onClick={handleClear}>清空</Button>
              </Space>
            </div>
            <Form
                form={form}
                layout="vertical"
                initialValues={{ apiNames: [], defaultValues: DEFAULT_VALUE_SEMANTICS }}
                className="single-review-form"
                onValuesChange={(_, values) => {
                  try {
                    window.localStorage.setItem(SINGLE_DRAFT_KEY, JSON.stringify(values))
                  } catch {
                    // 本地存储不可用时不影响当前编辑。
                  }
                }}
              >
                <Form.Item name="factorName" label="因子名称" rules={[{ required: true, message: '请输入因子名称' }]}>
                  <Input disabled={loading} placeholder="例如：近30日交易金额" />
                </Form.Item>
                <Form.Item
                  name="apiNames"
                  label={(
                    <Space size={8}>
                      <span>关联接口文档</span>
                      <Button type="link" size="small" onClick={() => void openSelectedApiDoc()}>查看已选接口</Button>
                    </Space>
                  )}
                  rules={[{ required: true, message: '至少选择一个接口文档' }]}
                >
                  <Select
                    disabled={loading}
                    mode="multiple"
                    showSearch
                    optionFilterProp="label"
                    placeholder="搜索并选择接口文档"
                    options={apiDocs.map((item) => ({
                      label: `${item.name}${item.category ? ` · ${item.category}` : ''}`,
                      value: item.name,
                    }))}
                  />
                </Form.Item>
                <Form.Item name="requirement" label="需求内容" rules={[{ required: true, message: '请输入需求内容' }]}>
                  <Input.TextArea disabled={loading} rows={4} placeholder="填写业务背景、使用场景和加工逻辑要求" />
                </Form.Item>
                <div className="review-content-fieldset">
                  <div className="review-content-fieldset-title">待评审内容</div>
                  <Form.Item name="paramFields" label="因子加工输入字段">
                    <Input.TextArea disabled={loading} rows={3} placeholder="每行一个字段，建议格式：字段名 | 字段来源 | 用途" />
                  </Form.Item>
                  <Form.Item name="procLogic" label="加工逻辑" rules={[{ required: true, message: '请输入加工逻辑' }]}>
                    <Input.TextArea disabled={loading} rows={6} placeholder="逐步填写待评审加工逻辑" />
                  </Form.Item>
                  <Form.Item name="defaultValues" label="默认值">
                    <Input.TextArea
                      disabled={loading}
                      rows={2}
                      placeholder="填写字段异常、无匹配记录等情况下的默认返回规则"
                    />
                  </Form.Item>
                </div>
                <div className="single-primary-actions">
                  <Button
                    size="large"
                    type="primary"
                    loading={oneClickRunning}
                    disabled={executionDisabled && !oneClickRunning}
                      onClick={handleOneClickReview}
                  >
                    {oneClickRunning ? '评审中...' : '开始评审'}
                  </Button>
                  {loading ? (
                    <Button danger loading={stoppingExecution} onClick={handleStopExecution}>终止执行</Button>
                  ) : null}
                  <Button disabled={!executionLogs.length} onClick={() => setExecutionLogOpen(true)}>执行详情</Button>
                  <Tag color={userStatusColor}>{userStatus}</Tag>
                </div>
              </Form>
            {error ? (
              <Alert
                className="single-inline-error"
                type="error"
                showIcon
                message="评审执行失败"
                description={error}
                action={<Button size="small" onClick={() => setExecutionLogOpen(true)}>查看执行详情</Button>}
              />
            ) : null}
          </section>
        </Col>

        <Col span={24} className="single-result-pane">
          <section className="work-surface single-result-surface" aria-label="评审结果">
            <div className="surface-heading">
              <div>
                <Typography.Title level={5}>评审结果</Typography.Title>
                <Typography.Text type="secondary">评审完成后直接展示报告正文。</Typography.Text>
              </div>
              <Space size={8}>
                <ArtifactDocumentButton fileId={phase1?.standardFileId} title="标准加工规范" onNotice={onNotice} />
                {phase2?.reviewFileId ? (
                  <Button href={`/outputs/${encodeURIComponent(phase2.reviewFileId)}`} download={phase2.reviewFileId}>
                    下载评审报告
                  </Button>
                ) : null}
              </Space>
            </div>
            <div className="single-result-content">
              {loadingPhase2 ? (
                <Spin />
              ) : result ? (
                <ReviewResultPanel
                  result={result}
                  phase1={phase1}
                  phase2={phase2}
                  onOpenArtifact={handleOpenArtifact}
                />
              ) : (
                <EmptyState title={loading ? '正在生成标准并执行评审…' : '填写左侧内容并开始评审。'} />
              )}
            </div>
          </section>
        </Col>
      </Row>

      <Modal
        title={drawerTitle}
        open={pageActive && drawerOpen}
        maskClosable={false}
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

      <Drawer
        title="接口文档"
        width="min(920px, 78vw)"
        open={pageActive && apiDocOpen}
        onClose={() => setApiDocOpen(false)}
        extra={(
          <Select
            style={{ width: 280 }}
            value={apiDocDetail?.name}
            placeholder="切换已选接口"
            options={(draftPreview?.apiNames || []).map((name) => ({ label: name, value: name }))}
            onChange={(name) => void openSelectedApiDoc(name)}
          />
        )}
      >
        <Spin spinning={apiDocLoading}>
          {apiDocDetail?.content ? (
            <MarkdownViewer content={apiDocDetail.content} fileName={`${apiDocDetail.name}.md`} />
          ) : (
            <EmptyState title="暂无接口文档内容。" />
          )}
        </Spin>
      </Drawer>

      <Modal
        title="执行日志"
        open={pageActive && executionLogOpen}
        maskClosable={false}
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
              label: `Claude 输入/输出（${debugLogCount}）`,
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
        open={pageActive && caseModalOpen}
        maskClosable={false}
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
        open={pageActive && clearConfirmOpen}
        maskClosable={false}
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
