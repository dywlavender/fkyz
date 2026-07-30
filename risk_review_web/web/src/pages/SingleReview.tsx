import {
  Alert,
  Button,
  Col,
  Form,
  Modal,
  Row,
  Space,
  Table,
  Tag,
  Typography,
  message,
} from 'antd'
import { useEffect, useMemo, useRef, useState } from 'react'

import { usePageActive } from '../components/AppLayout'
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
import { buildDraftFromSections, parseDraftSections, parseReviewResult, taskFailureMessage } from '../utils/reviewTransform'
import {
  ApiDocumentDrawer,
  buildCaseColumns,
  caseDisplayName,
  caseFactorName,
  caseId,
  caseKey,
  CaseSelectionModal,
  ClearPageConfirmModal,
  DEFAULT_VALUE_SEMANTICS,
  RUN_INVALIDATED,
  SINGLE_DRAFT_KEY,
  SingleReviewInputPanel,
  SingleReviewResultSurface,
  ExecutionLogModal,
  RawArtifactModal,
  type CaseOption,
  type ExecutionLogItem,
  type ReviewFormValues,
  type SingleReviewProps,
} from '../features/single-review'

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
          prompt: '模型实际输入',
          stdout: '模型最终输出',
          stderr: '模型错误输出',
        }
        return {
          id: taskLogId(taskId, phase, 'debug', index, log),
          time: taskLogTime(log),
          phase,
          level: (stream === 'stderr' && log.message ? 'error' : 'info') as ExecutionLogItem['level'],
          message: labels[stream] || 'Agent 交互',
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
  const caseColumns = buildCaseColumns()
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
          <SingleReviewInputPanel
            form={form}
            apiDocs={apiDocs}
            loading={loading}
            executionDisabled={executionDisabled}
            oneClickRunning={oneClickRunning}
            stoppingExecution={stoppingExecution}
            hasExecutionLogs={Boolean(executionLogs.length)}
            userStatus={userStatus}
            userStatusColor={userStatusColor}
            error={error}
            onLoadCase={() => void openCaseModal()}
            onClear={handleClear}
            onOpenApiDoc={() => void openSelectedApiDoc()}
            onStartReview={() => void handleOneClickReview()}
            onStopExecution={() => void handleStopExecution()}
            onOpenExecutionLogs={() => setExecutionLogOpen(true)}
            onValuesChange={(values) => {
              try {
                window.localStorage.setItem(SINGLE_DRAFT_KEY, JSON.stringify(values))
              } catch {
                // 本地存储不可用时不影响当前编辑。
              }
            }}
          />
        </Col>

        <Col span={24} className="single-result-pane">
          <SingleReviewResultSurface
            loading={loading}
            loadingPhase2={loadingPhase2}
            result={result}
            phase1={phase1}
            phase2={phase2}
            onNotice={onNotice}
            onOpenArtifact={(fileId) => void handleOpenArtifact(fileId)}
          />
        </Col>
      </Row>

      <RawArtifactModal active={pageActive} open={drawerOpen} title={drawerTitle} content={drawerContent} onClose={() => setDrawerOpen(false)} />
      <ApiDocumentDrawer
        active={pageActive}
        open={apiDocOpen}
        loading={apiDocLoading}
        detail={apiDocDetail}
        selectedNames={draftPreview?.apiNames || []}
        onClose={() => setApiDocOpen(false)}
        onSelect={(name) => void openSelectedApiDoc(name)}
      />
      <ExecutionLogModal active={pageActive} open={executionLogOpen} logs={executionLogs} onClose={() => setExecutionLogOpen(false)} />
      <CaseSelectionModal
        active={pageActive}
        open={caseModalOpen}
        loading={caseLoading}
        executionDisabled={executionDisabled}
        query={caseQuery}
        options={filteredCaseOptions}
        columns={caseColumns}
        selectedKey={selectedCaseId}
        rowKey={caseKey}
        onQueryChange={setCaseQuery}
        onSelect={setSelectedCaseId}
        onClose={() => setCaseModalOpen(false)}
        onConfirm={handleLoadCase}
      />
      <ClearPageConfirmModal active={pageActive} open={clearConfirmOpen} executionDisabled={executionDisabled} onClose={() => setClearConfirmOpen(false)} onConfirm={confirmClearPage} />

    </Space>
  )
}
