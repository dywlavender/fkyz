import { DeleteOutlined, EditOutlined, EyeOutlined, PlayCircleOutlined, PlusOutlined, ReloadOutlined, StopOutlined, UploadOutlined } from '@ant-design/icons'
import {
  Alert,
  Button,
  Card,
  Col,
  Descriptions,
  Drawer,
  Empty,
  Form,
  Input,
  InputNumber,
  Modal,
  Progress,
  Row,
  Select,
  Space,
  Statistic,
  Table,
  Tag,
  Tooltip,
  Typography,
  Upload,
} from 'antd'
import type { ColumnsType } from 'antd/es/table'
import { useEffect, useMemo, useState } from 'react'

import {
  batchDeleteValidationCases,
  cancelTask,
  createValidationCase,
  deleteValidationCase,
  fetchValidationCases,
  fetchValidationRuns,
  fetchArtifactContent,
  fetchTask,
  fetchTasks,
  getClientId,
  importValidationCases,
  updateValidationCase,
  AppError,
  runRegression,
} from '../api/reviewApi'
import { HeaderActions, usePageActive, type PageKey } from '../components/AppLayout'
import { MarkdownViewer } from '../components/MarkdownViewer'
import type {
  AppNotice,
  CreateValidationCaseRequest,
  ExampleItem,
  RegressionTaskItem,
  RegressionTaskResult,
  TaskRecord,
  ValidationCase,
  ValidationCaseImportResult,
  ValidationRun,
} from '../types/review'

interface ValidationSetProps {
  headerPage?: PageKey
  examples: ExampleItem[]
  onNotice: (notice: AppNotice) => void
}

type CaseFormValues = CreateValidationCaseRequest & {
  apiDocIdsText?: string
  businessRuleIdsText?: string
  tagsText?: string
  expectedIssuePatternsText?: string
  forbiddenIssuePatternsText?: string
}

function formatDate(value?: string) {
  if (!value) return '-'
  return new Date(value).toLocaleString('zh-CN', { hour12: false })
}

function conclusionColor(value?: string) {
  const normalized = normalizeConclusion(value)
  if (normalized.includes('|')) return 'processing'
  if (normalized === 'pass') return 'success'
  if (normalized === 'fail') return 'error'
  if (normalized === 'unreviewable') return 'warning'
  return 'default'
}

function splitText(value?: string) {
  return String(value || '')
    .split(/[\n,，、]/)
    .map((item) => item.trim())
    .filter(Boolean)
}

function joinValues(value?: string[]) {
  return (value || []).join('\n')
}

function CompactDateStat({ title, value }: { title: string; value?: string }) {
  return (
    <div className="compact-date-stat">
      <div className="compact-date-stat-title">{title}</div>
      <div className="compact-date-stat-value" title={formatDate(value)}>{formatDate(value)}</div>
    </div>
  )
}

const ALL_VALUE = '__all__'
const REGRESSION_TASK_STORAGE_KEY = 'risk-review-validation-regression-task-id'
const TERMINAL_TASK_STATUSES = new Set(['done', 'error', 'cancelled'])
type RegressionDocumentType = 'review' | 'standard'

function isTerminalTask(task?: TaskRecord) {
  return TERMINAL_TASK_STATUSES.has(String(task?.status || '').toLowerCase())
}

function taskStatusLabel(value?: string) {
  const labels: Record<string, string> = {
    queued: '排队中',
    running: '执行中',
    cancelling: '取消中',
    cancelled: '已取消',
    done: '已完成',
    error: '执行失败',
  }
  return labels[String(value || '').toLowerCase()] || value || '未知'
}

function taskStatusColor(value?: string) {
  const status = String(value || '').toLowerCase()
  if (status === 'done') return 'success'
  if (status === 'error' || status === 'cancelled') return 'error'
  if (status === 'queued' || status === 'running' || status === 'cancelling') return 'processing'
  return 'default'
}

function normalizeConclusion(value?: string) {
  const source = String(value || 'unreviewable').trim()
  const candidates: string[] = []
  if (/✅|\bpass\b|通过/.test(source) && !/不通过/.test(source)) candidates.push('pass')
  if (/❌|\bfail\b|不通过|不符合/.test(source)) candidates.push('fail')
  if (/⛔|无法评审|\bunreviewable\b|\bunknown\b/.test(source)) candidates.push('unreviewable')
  if (candidates.length) return [...new Set(candidates)].join('|')
  const normalized = source.toLowerCase()
  if (normalized === 'unknown') return 'unreviewable'
  return normalized || 'unreviewable'
}

function conclusionLabel(value?: string) {
  const labels: Record<string, string> = {
    pass: '通过',
    fail: '不通过',
    unreviewable: '无法评审',
  }
  const normalized = normalizeConclusion(value)
  if (normalized.includes('|')) {
    return normalized.split('|').map((item) => labels[item] || item).join(' / ')
  }
  return labels[normalized] || normalized
}

function conclusionIncludes(expected?: string, actual?: string) {
  const expectedValues = normalizeConclusion(expected)
    .split('|')
    .map((item) => item.trim())
    .filter(Boolean)
  return expectedValues.includes(normalizeConclusion(actual))
}

function isTerminalRun(run?: ValidationRun) {
  return ['success', 'failed', 'partial_success'].includes(String(run?.status || ''))
}

function runMatched(run?: ValidationRun) {
  if (!run || !isTerminalRun(run)) return undefined
  if (typeof run.matched === 'boolean') return run.matched
  if (run.status === 'success' && run.actualConclusion) {
    return conclusionIncludes(run.expectedConclusion, run.actualConclusion)
  }
  return run.matched
}

function runAlertType(run?: ValidationRun): 'success' | 'info' | 'warning' | 'error' {
  if (!run) return 'info'
  if (!isTerminalRun(run)) return 'info'
  if (run.status !== 'success') return 'error'
  return runMatched(run) ? 'success' : 'warning'
}

function runDescription(run: ValidationRun) {
  if (run.error) return run.error
  const comparison = `期望 ${conclusionLabel(run.expectedConclusion)}，实际 ${conclusionLabel(run.actualConclusion)}`
  if (!isTerminalRun(run)) return `${comparison}，执行中，尚未形成最终一致性判断`
  return `${comparison}，${runMatched(run) ? '符合预期' : '不符合预期'}`
}

function matchTag(run?: ValidationRun) {
  if (!run) return <Tag>未执行</Tag>
  if (!isTerminalRun(run)) return <Tag color="processing">执行中</Tag>
  const matched = runMatched(run)
  if (matched === true) return <Tag color="success">一致</Tag>
  if (matched === false) return <Tag color="error">不一致</Tag>
  return <Tag>未执行</Tag>
}

export function ValidationSet({ headerPage = 'validation-set', examples, onNotice }: ValidationSetProps) {
  const pageActive = usePageActive()
  const [form] = Form.useForm<CaseFormValues>()
  const [cases, setCases] = useState<ValidationCase[]>([])
  const [runs, setRuns] = useState<ValidationRun[]>([])
  const [loading, setLoading] = useState(false)
  const [importing, setImporting] = useState(false)
  const [importResult, setImportResult] = useState<ValidationCaseImportResult | null>(null)
  const [loadError, setLoadError] = useState<string>()
  const [runningIds, setRunningIds] = useState<string[]>([])
  const [selectedRowKeys, setSelectedRowKeys] = useState<React.Key[]>([])
  const [detailCase, setDetailCase] = useState<ValidationCase | null>(null)
  const [editingCase, setEditingCase] = useState<ValidationCase | null>(null)
  const [editorOpen, setEditorOpen] = useState(false)
  const [resultOpen, setResultOpen] = useState(false)
  const [activeRun, setActiveRun] = useState<ValidationRun | null>(null)
  const [activeRunDocument, setActiveRunDocument] = useState<RegressionDocumentType>('review')
  const [regressionTaskId, setRegressionTaskId] = useState<string>()
  const [regressionTask, setRegressionTask] = useState<TaskRecord<RegressionTaskResult> | null>(null)
  const [regressionRecovering, setRegressionRecovering] = useState(false)
  const [regressionCancelling, setRegressionCancelling] = useState(false)
  const [activeRegressionItem, setActiveRegressionItem] = useState<RegressionTaskItem | null>(null)
  const [activeRegressionDocument, setActiveRegressionDocument] = useState<RegressionDocumentType>('review')
  const [regressionContentCache, setRegressionContentCache] = useState<Record<string, string>>({})
  const [loadingRegressionFile, setLoadingRegressionFile] = useState<string>()
  const [query, setQuery] = useState('')
  const [conclusionFilter, setConclusionFilter] = useState(ALL_VALUE)
  const [matchFilter, setMatchFilter] = useState(ALL_VALUE)
  const [casePage, setCasePage] = useState(1)
  const [casePageSize, setCasePageSize] = useState(10)

  const activeRegressionFileId = activeRegressionDocument === 'review'
    ? activeRegressionItem?.reviewFileId
    : activeRegressionItem?.standardFileId
  const activeRegressionInlineContent = activeRegressionDocument === 'review'
    ? activeRegressionItem?.raw || activeRegressionItem?.reportPreview
    : activeRegressionItem?.standardText
  const activeRegressionContent = activeRegressionInlineContent
    || (activeRegressionFileId ? regressionContentCache[activeRegressionFileId] : '')
  const activeRunFileId = activeRunDocument === 'review' ? activeRun?.reviewFile : activeRun?.standardFile
  const activeRunContent = activeRunFileId ? regressionContentCache[activeRunFileId] : ''

  useEffect(() => {
    if (!activeRegressionItem || activeRegressionInlineContent || !activeRegressionFileId || regressionContentCache[activeRegressionFileId]) return
    setLoadingRegressionFile(activeRegressionFileId)
    fetchArtifactContent(activeRegressionFileId)
      .then((content) => setRegressionContentCache((current) => ({ ...current, [activeRegressionFileId]: content })))
      .catch((caught) => {
        const message = caught instanceof Error ? caught.message : '读取产物失败'
        onNotice({ type: 'error', message })
      })
      .finally(() => setLoadingRegressionFile((current) => (current === activeRegressionFileId ? undefined : current)))
  }, [activeRegressionFileId, activeRegressionInlineContent, activeRegressionItem, onNotice, regressionContentCache])

  useEffect(() => {
    if (!resultOpen || !activeRunFileId || regressionContentCache[activeRunFileId]) return
    setLoadingRegressionFile(activeRunFileId)
    fetchArtifactContent(activeRunFileId)
      .then((content) => setRegressionContentCache((current) => ({ ...current, [activeRunFileId]: content })))
      .catch((caught) => {
        const message = caught instanceof Error ? caught.message : '读取产物失败'
        onNotice({ type: 'error', message })
      })
      .finally(() => setLoadingRegressionFile((current) => (current === activeRunFileId ? undefined : current)))
  }, [activeRunFileId, onNotice, regressionContentCache, resultOpen])

  function openRegressionResult(item: RegressionTaskItem) {
    setActiveRegressionDocument('review')
    setActiveRegressionItem(item)
  }

  async function loadAll() {
    setLoading(true)
    setLoadError(undefined)
    try {
      const [nextCases, nextRuns] = await Promise.all([
        fetchValidationCases(),
        fetchValidationRuns(),
      ])
      setCases(nextCases)
      setRuns(nextRuns)
    } catch (caught) {
      const message = caught instanceof Error ? caught.message : '加载验证集失败'
      setLoadError(message)
      onNotice({ type: 'error', message })
    } finally {
      setLoading(false)
    }
  }

  useEffect(() => {
    void loadAll()
  }, [])

  useEffect(() => {
    let disposed = false

    async function restoreRegressionTask() {
      setRegressionRecovering(true)
      try {
        let task: TaskRecord<RegressionTaskResult> | undefined
        const storedTaskId = window.localStorage.getItem(REGRESSION_TASK_STORAGE_KEY) || ''
        if (storedTaskId) {
          try {
            task = await fetchTask(storedTaskId) as TaskRecord<RegressionTaskResult>
          } catch (caught) {
            if (!(caught instanceof AppError) || caught.status !== 404) throw caught
            window.localStorage.removeItem(REGRESSION_TASK_STORAGE_KEY)
          }
        }

        if (!task) {
          const clientId = getClientId()
          task = (await fetchTasks())
            .filter((item) => item.taskType === 'regression' && item.clientId === clientId)
            .sort((left, right) => (right.created || 0) - (left.created || 0))[0] as TaskRecord<RegressionTaskResult> | undefined
        }

        if (disposed || !task?.id) return
        window.localStorage.setItem(REGRESSION_TASK_STORAGE_KEY, task.id)
        setRegressionTaskId(task.id)
        setRegressionTask(task)
        setRunningIds(isTerminalTask(task) ? [] : task.caseIds || [])
        if (isTerminalTask(task)) setRegressionCancelling(false)
      } catch (caught) {
        if (disposed) return
        const message = caught instanceof Error ? caught.message : '恢复验证任务失败'
        setLoadError(message)
      } finally {
        if (!disposed) setRegressionRecovering(false)
      }
    }

    void restoreRegressionTask()
    return () => {
      disposed = true
    }
  }, [])

  useEffect(() => {
    if (!regressionTaskId) return
    const activeTaskId = regressionTaskId
    let disposed = false
    let timer: number | undefined

    async function refreshRegressionTask() {
      try {
        const task = await fetchTask(activeTaskId) as TaskRecord<RegressionTaskResult>
        if (disposed) return
        setRegressionTask(task)
        setRunningIds(isTerminalTask(task) ? [] : task.caseIds || [])
        if (!isTerminalTask(task)) {
          timer = window.setTimeout(refreshRegressionTask, 1200)
        } else {
          setRegressionCancelling(false)
          const nextRuns = await fetchValidationRuns()
          if (!disposed) setRuns(nextRuns)
        }
      } catch (caught) {
        if (disposed) return
        if (caught instanceof AppError && caught.status === 404) {
          window.localStorage.removeItem(REGRESSION_TASK_STORAGE_KEY)
          setRegressionTaskId(undefined)
          setRegressionTask(null)
          setRunningIds([])
          setRegressionCancelling(false)
          return
        }
        setLoadError(caught instanceof Error ? caught.message : '验证任务轮询失败')
      }
    }

    void refreshRegressionTask()
    return () => {
      disposed = true
      if (timer) window.clearTimeout(timer)
    }
  }, [regressionTaskId])

  const latestRunByCase = useMemo(() => {
    const map = new Map<string, ValidationRun>()
    for (const run of runs) {
      const current = map.get(run.caseId)
      const currentTime = current?.finishedAt || current?.startedAt || ''
      const nextTime = run.finishedAt || run.startedAt || ''
      if (!current || nextTime >= currentTime) {
        map.set(run.caseId, run)
      }
    }
    return map
  }, [runs])

  const regressionActive = Boolean(regressionTask && !isTerminalTask(regressionTask))
  const regressionItems = regressionTask?.result?.items || []
  const regressionSummary = regressionTask?.result?.summary
  const regressionTotal = regressionSummary?.total ?? regressionTask?.caseIds?.length ?? regressionItems.length
  const regressionCompleted = (regressionSummary?.pass || 0)
    + (regressionSummary?.fail || 0)
    + (regressionSummary?.error || 0)
    + (regressionSummary?.skip || 0)
  const regressionProgress = regressionTotal ? Math.min(100, Math.round((regressionCompleted / regressionTotal) * 100)) : 0

  const stats = useMemo(() => {
    const latest = cases.map((item) => latestRunByCase.get(item.caseId)).filter(Boolean) as ValidationRun[]
    const completed = latest.filter(isTerminalRun)
    const matched = completed.filter((run) => runMatched(run) === true).length
    return {
      total: cases.length,
      passed: completed.filter((run) => normalizeConclusion(run.actualConclusion) === 'pass').length,
      failed: completed.filter((run) => normalizeConclusion(run.actualConclusion) === 'fail').length,
      unreviewable: completed.filter((run) => normalizeConclusion(run.actualConclusion) === 'unreviewable').length,
      agreementRate: completed.length ? Math.round((matched / completed.length) * 1000) / 10 : null,
      recent: latest
        .map((run) => run.finishedAt || run.startedAt || '')
        .sort()
        .pop(),
    }
  }, [cases, latestRunByCase])

  const filteredCases = useMemo(() => {
    const keyword = query.trim().toLowerCase()
    return cases.filter((item) => {
      const latestRun = latestRunByCase.get(item.caseId)
      const text = [
        item.caseId,
        item.caseName,
        item.factorName,
        item.requirement,
        item.reviewContent,
        item.expectedConclusion,
        item.remark,
        ...(item.tags || []),
      ].filter(Boolean).join(' ').toLowerCase()
      if (keyword && !text.includes(keyword)) return false
      if (conclusionFilter !== ALL_VALUE && normalizeConclusion(item.expectedConclusion) !== conclusionFilter) return false
      if (matchFilter === 'matched' && runMatched(latestRun) !== true) return false
      if (matchFilter === 'mismatched' && runMatched(latestRun) !== false) return false
      if (matchFilter === 'unrun' && latestRun) return false
      if (matchFilter === 'unreviewable' && normalizeConclusion(latestRun?.actualConclusion) !== 'unreviewable') return false
      return true
    })
  }, [cases, conclusionFilter, latestRunByCase, matchFilter, query])

  useEffect(() => {
    setCasePage(1)
  }, [query, conclusionFilter, matchFilter])

  useEffect(() => {
    const maxPage = Math.max(1, Math.ceil(filteredCases.length / casePageSize))
    if (casePage > maxPage) setCasePage(maxPage)
  }, [casePage, casePageSize, filteredCases.length])

  function openCreator() {
    setEditingCase(null)
    form.resetFields()
    form.setFieldsValue({ expectedConclusion: 'pass' })
    setEditorOpen(true)
  }

  function openEditor(record: ValidationCase) {
    setEditingCase(record)
    form.setFieldsValue({
      caseName: record.caseName,
      factorName: record.factorName,
      requirement: record.requirement,
      reviewContent: record.reviewContent || '',
      expectedConclusion: normalizeConclusion(record.expectedConclusion),
      expectedIssuePatternsText: joinValues(record.expectedIssuePatterns),
      forbiddenIssuePatternsText: joinValues(record.forbiddenIssuePatterns),
      maxIssueCount: record.maxIssueCount,
      apiDocIdsText: joinValues(record.apiDocIds),
      businessRuleIdsText: joinValues(record.businessRuleIds),
      tagsText: joinValues(record.tags),
      remark: record.remark || '',
    })
    setEditorOpen(true)
  }

  async function saveCase() {
    try {
      const values = await form.validateFields()
      const payload: CreateValidationCaseRequest = {
        caseName: values.caseName,
        factorName: values.factorName,
        requirement: values.requirement,
        reviewContent: values.reviewContent,
        expectedConclusion: normalizeConclusion(values.expectedConclusion),
        expectedIssuePatterns: splitText(values.expectedIssuePatternsText),
        forbiddenIssuePatterns: splitText(values.forbiddenIssuePatternsText),
        maxIssueCount: values.maxIssueCount,
        apiDocIds: splitText(values.apiDocIdsText),
        businessRuleIds: splitText(values.businessRuleIdsText),
        tags: splitText(values.tagsText),
        remark: values.remark || '',
      }
      if (editingCase) {
        await updateValidationCase(editingCase.caseId, payload)
        onNotice({ type: 'success', message: '验证案例已更新' })
      } else {
        await createValidationCase(payload)
        onNotice({ type: 'success', message: '验证案例已新增' })
      }
      setEditorOpen(false)
      await loadAll()
    } catch (caught) {
      const message = caught instanceof Error ? caught.message : '保存验证案例失败'
      onNotice({ type: 'error', message })
    }
  }

  async function handleImportCases(file: File) {
    setImporting(true)
    try {
      const result = await importValidationCases(file)
      setImportResult(result)
      onNotice({
        type: result.failed ? 'warning' : 'success',
        message: `案例导入完成：成功 ${result.success}，失败 ${result.failed}`,
      })
      if (result.success) await loadAll()
    } catch (caught) {
      const message = caught instanceof Error ? caught.message : '导入验证案例失败'
      onNotice({ type: 'error', message })
    } finally {
      setImporting(false)
    }
    return false
  }

  function confirmDelete(record: ValidationCase) {
    Modal.confirm({
      title: '确认删除验证案例？',
      content: `将删除「${record.caseName}」，历史运行记录会保留。`,
      okText: '删除',
      cancelText: '取消',
      okButtonProps: { danger: true },
      onOk: async () => {
        try {
          await deleteValidationCase(record.caseId)
          onNotice({ type: 'success', message: '验证案例已删除' })
          await loadAll()
        } catch (caught) {
          const message = caught instanceof Error ? caught.message : '删除验证案例失败'
          onNotice({ type: 'error', message })
        }
      },
    })
  }

  async function executeOne(record: ValidationCase) {
    if (regressionActive) {
      onNotice({ type: 'info', message: '已有验证任务正在执行，请等待当前任务结束' })
      return
    }
    setRunningIds((ids) => [...ids, record.caseId])
    let regressionSubmitted = false
    try {
      const response = await runRegression({ exampleIds: [record.caseId] })
      regressionSubmitted = true
      window.localStorage.setItem(REGRESSION_TASK_STORAGE_KEY, response.taskId)
      setRegressionTaskId(response.taskId)
      setRegressionTask(response.task as TaskRecord<RegressionTaskResult>)
      onNotice({ type: 'success', message: '验证任务已提交，切换页面后仍可返回查看进度' })
    } catch (caught) {
      const message = caught instanceof Error ? caught.message : '验证执行失败'
      onNotice({ type: 'error', message })
    } finally {
      if (!regressionSubmitted) {
        setRunningIds((ids) => ids.filter((id) => id !== record.caseId))
      }
    }
  }

  function confirmBatchDelete() {
    Modal.confirm({
      title: '确认批量删除验证案例？',
      content: `将删除已选 ${selectedRowKeys.length} 条案例，历史运行记录会保留。`,
      okText: '批量删除',
      cancelText: '取消',
      okButtonProps: { danger: true },
      onOk: async () => {
        try {
          const result = await batchDeleteValidationCases(selectedRowKeys.map(String))
          onNotice({ type: result.failed ? 'warning' : 'success', message: `删除完成：成功 ${result.success}，失败 ${result.failed}` })
          setSelectedRowKeys([])
          await loadAll()
        } catch (caught) {
          const message = caught instanceof Error ? caught.message : '批量删除失败'
          onNotice({ type: 'error', message })
        }
      },
    })
  }

  async function executeBatch() {
    const ids = selectedRowKeys.map(String)
    if (regressionActive) {
      onNotice({ type: 'info', message: '已有验证任务正在执行，请等待当前任务结束' })
      return
    }
    setRunningIds(ids)
    let regressionSubmitted = false
    try {
      const response = await runRegression({ exampleIds: ids })
      regressionSubmitted = true
      window.localStorage.setItem(REGRESSION_TASK_STORAGE_KEY, response.taskId)
      setRegressionTaskId(response.taskId)
      setRegressionTask(response.task as TaskRecord<RegressionTaskResult>)
      onNotice({ type: 'success', message: `已提交 ${ids.length} 条回归验证，切换页面后仍可返回查看进度` })
    } catch (caught) {
      const message = caught instanceof Error ? caught.message : '批量执行失败'
      onNotice({ type: 'error', message })
    } finally {
      if (!regressionSubmitted) setRunningIds([])
    }
  }

  async function refreshRegressionSnapshot() {
    if (!regressionTaskId) return
    setRegressionRecovering(true)
    try {
      const task = await fetchTask(regressionTaskId) as TaskRecord<RegressionTaskResult>
      setRegressionTask(task)
      setRunningIds(isTerminalTask(task) ? [] : task.caseIds || [])
    } catch (caught) {
      const message = caught instanceof Error ? caught.message : '刷新验证任务失败'
      onNotice({ type: 'error', message })
    } finally {
      setRegressionRecovering(false)
    }
  }

  function terminateRegression() {
    if (!regressionTaskId || !regressionActive || regressionCancelling) return
    Modal.confirm({
      title: '确认终止本次回归验证？',
      content: '排队中的案例会被取消，正在运行的 Claude 调用会被终止，已完成结果仍会保留。',
      okText: '终止执行',
      okButtonProps: { danger: true },
      cancelText: '继续执行',
      onOk: async () => {
        setRegressionCancelling(true)
        try {
          const task = await cancelTask(regressionTaskId) as TaskRecord<RegressionTaskResult>
          setRegressionTask(task)
          setRunningIds(isTerminalTask(task) ? [] : task.caseIds || [])
          if (isTerminalTask(task)) setRegressionCancelling(false)
          onNotice({ type: 'warning', message: '已请求终止回归验证，正在停止运行中的调用' })
        } catch (caught) {
          setRegressionCancelling(false)
          onNotice({ type: 'error', message: caught instanceof Error ? caught.message : '终止回归验证失败' })
          throw caught
        }
      },
    })
  }

  function openRunResult(run?: ValidationRun) {
    if (!run) return
    setActiveRunDocument('review')
    setActiveRun(run)
    setResultOpen(true)
  }

  const detailRun = detailCase ? latestRunByCase.get(detailCase.caseId) : undefined

  const columns: ColumnsType<ValidationCase> = [
    { title: '案例名称', dataIndex: 'caseName', ellipsis: true, render: (value?: string) => value || '-' },
    { title: '因子名称', dataIndex: 'factorName', ellipsis: true, render: (value?: string) => value || '-' },
    {
      title: '期望结论',
      dataIndex: 'expectedConclusion',
      width: 120,
      render: (value?: string) => <Tag color={conclusionColor(value)}>{conclusionLabel(value)}</Tag>,
    },
    {
      title: '最近实际结论',
      width: 130,
      render: (_value, record) => {
        const run = latestRunByCase.get(record.caseId)
        if (!run) return <Tag>未执行</Tag>
        if (!isTerminalRun(run)) return <Tag color="processing">执行中</Tag>
        return <Tag color={conclusionColor(run.actualConclusion)}>{conclusionLabel(run.actualConclusion)}</Tag>
      },
    },
    {
      title: '是否一致',
      width: 120,
      render: (_value, record) => matchTag(latestRunByCase.get(record.caseId)),
    },
    {
      title: '标签',
      dataIndex: 'tags',
      width: 180,
      render: (tags?: string[]) => tags?.length ? <Space size={4} wrap>{tags.slice(0, 3).map((tag) => <Tag key={tag}>{tag}</Tag>)}</Space> : '-',
    },
    {
      title: '最近执行时间',
      width: 180,
      render: (_value, record) => formatDate(latestRunByCase.get(record.caseId)?.finishedAt || latestRunByCase.get(record.caseId)?.startedAt),
    },
    {
      title: '操作',
      width: 280,
      render: (_value, record) => {
        const latestRun = latestRunByCase.get(record.caseId)
        return (
          <Space wrap onClick={(event) => event.stopPropagation()}>
            <Button
              type="link"
              icon={<PlayCircleOutlined />}
              disabled={regressionActive && !runningIds.includes(record.caseId)}
              loading={runningIds.includes(record.caseId)}
              onClick={() => executeOne(record)}
            >
              执行验证
            </Button>
            <Button type="link" icon={<EyeOutlined />} disabled={!latestRun} onClick={() => openRunResult(latestRun)}>
              查看结果
            </Button>
            <Button type="link" icon={<EditOutlined />} onClick={() => openEditor(record)}>编辑</Button>
            <Button danger type="link" icon={<DeleteOutlined />} onClick={() => confirmDelete(record)}>删除</Button>
          </Space>
        )
      },
    },
  ]

  return (
    <Space className="app-page" direction="vertical" size={12} style={{ width: '100%' }}>
      <HeaderActions page={headerPage}>
        <Space className="app-header-action-space" size={8} wrap>
          <Button type="primary" icon={<PlusOutlined />} onClick={openCreator}>新增案例</Button>
          <Tooltip title="支持 CSV / XLSX；可直接提供待评审内容，或分别提供因子加工输入字段、加工逻辑和默认值">
            <Upload beforeUpload={handleImportCases} accept=".csv,.xlsx" showUploadList={false} maxCount={1}>
              <Button icon={<UploadOutlined />} loading={importing}>导入案例</Button>
            </Upload>
          </Tooltip>
          <Button icon={<ReloadOutlined />} loading={loading} onClick={loadAll}>刷新</Button>
        </Space>
      </HeaderActions>

      <Row gutter={[12, 12]}>
        <Col xs={24} sm={12} xl={4}><Card className="compact-card"><Statistic title="案例总数" value={stats.total} /></Card></Col>
        <Col xs={24} sm={12} xl={4}><Card className="compact-card"><Statistic title="最近通过" value={stats.passed} /></Card></Col>
        <Col xs={24} sm={12} xl={4}><Card className="compact-card"><Statistic title="最近失败" value={stats.failed} /></Card></Col>
        <Col xs={24} sm={12} xl={4}><Card className="compact-card"><Statistic title="无法评审" value={stats.unreviewable} /></Card></Col>
        <Col xs={24} sm={12} xl={4}><Card className="compact-card"><Statistic title="一致率" value={stats.agreementRate ?? '暂无'} suffix={stats.agreementRate == null ? undefined : '%'} precision={1} /></Card></Col>
        <Col xs={24} sm={12} xl={4}><Card className="compact-card"><CompactDateStat title="最近执行时间" value={stats.recent} /></Card></Col>
      </Row>

      {regressionTask || regressionRecovering ? (
        <Card
          className="compact-card"
          title="最近一次回归验证任务"
          extra={(
            <Space size={8}>
              <Button
                danger
                size="small"
                icon={<StopOutlined />}
                loading={regressionCancelling}
                disabled={!regressionTaskId || !regressionActive}
                onClick={terminateRegression}
              >
                终止执行
              </Button>
              <Button
                size="small"
                icon={<ReloadOutlined />}
                loading={regressionRecovering}
                disabled={!regressionTaskId}
                onClick={() => void refreshRegressionSnapshot()}
              >
                刷新进度
              </Button>
            </Space>
          )}
        >
          {regressionTask ? (
            <Space direction="vertical" size={12} style={{ width: '100%' }}>
              <Alert
                showIcon
                type={regressionTask.status === 'error' ? 'error' : isTerminalTask(regressionTask) ? 'success' : 'info'}
                message={(
                  <Space wrap>
                    <span>任务状态</span>
                    <Tag color={taskStatusColor(regressionTask.status)}>{taskStatusLabel(regressionTask.status)}</Tag>
                    <Typography.Text type="secondary">{regressionSummary?.currentTitle || ''}</Typography.Text>
                  </Space>
                )}
                description={regressionTask.result?.stderr || (regressionActive ? '任务仍在后端执行，离开页面不会中断；返回验证集页面会自动恢复。' : '任务结果已保留，可查看每条案例的实际结论和报告。')}
              />
              <Descriptions bordered column={{ xs: 1, sm: 2, lg: 4 }} size="small">
                <Descriptions.Item label="Task ID">{regressionTask.id || '-'}</Descriptions.Item>
                <Descriptions.Item label="总数">{regressionTotal}</Descriptions.Item>
                <Descriptions.Item label="通过">{regressionSummary?.pass || 0}</Descriptions.Item>
                <Descriptions.Item label="未通过">{(regressionSummary?.fail || 0) + (regressionSummary?.error || 0)}</Descriptions.Item>
              </Descriptions>
              <Progress
                percent={regressionProgress}
                status={regressionTask.status === 'error' ? 'exception' : regressionTask.status === 'done' ? 'success' : 'active'}
                format={() => `${regressionCompleted} / ${regressionTotal}`}
              />
              <Table<RegressionTaskItem>
                rowKey={(record, index) => record.id || `regression-${index}`}
                className="compact-table"
                size="small"
                pagination={{ pageSize: 5, size: 'small' }}
                dataSource={regressionItems}
                columns={[
                  { title: '案例', dataIndex: 'title', ellipsis: true, render: (value?: string, record?: RegressionTaskItem) => value || record?.id || '-' },
                  {
                    title: '实际结论',
                    dataIndex: 'verdict',
                    width: 110,
                    render: (value?: string) => value ? conclusionLabel(value) : '-',
                  },
                  {
                    title: '期望匹配',
                    width: 130,
                    render: (_value, record) => {
                      const status = String(record.status || '').toLowerCase()
                      if (status === 'running') return <Tag color="processing">执行中</Tag>
                      if (status === 'error') return <Tag color="error">执行失败</Tag>
                      if (status === 'skip') return <Tag>已取消</Tag>
                      if (record.expectedOk || status === 'pass') return <Tag color="success">符合预期</Tag>
                      if (status === 'fail') return <Tag color="error">不符合预期</Tag>
                      return <Tag>待执行</Tag>
                    },
                  },
                  {
                    title: '结果',
                    width: 100,
                    render: (_value, record) => (
                      <Button type="link" size="small" disabled={!record.raw && !record.reviewFileId} onClick={() => openRegressionResult(record)}>
                        查看
                      </Button>
                    ),
                  },
                ]}
              />
            </Space>
          ) : (
            <Typography.Text type="secondary">正在恢复最近一次验证任务...</Typography.Text>
          )}
        </Card>
      ) : null}

      {loadError ? (
        <Alert
          showIcon
          type="error"
          message="验证集加载失败"
          description={loadError}
          action={<Button size="small" onClick={loadAll}>重试</Button>}
        />
      ) : null}

      <Card className="compact-card" title="操作栏">
        <Space className="responsive-filters" wrap size={8} style={{ width: '100%' }}>
          <Input.Search
            className="responsive-filter-search responsive-filter-search-wide"
            allowClear
            placeholder="搜索案例名称 / 因子 / 标签 / 备注"
            value={query}
            onChange={(event) => setQuery(event.target.value)}
          />
          <Select
            className="responsive-filter-select"
            value={conclusionFilter}
            onChange={setConclusionFilter}
            options={[
              { label: '全部期望结论', value: ALL_VALUE },
              { label: '通过', value: 'pass' },
              { label: '不通过', value: 'fail' },
              { label: '无法评审', value: 'unreviewable' },
            ]}
          />
          <Select
            className="responsive-filter-select"
            value={matchFilter}
            onChange={setMatchFilter}
            options={[
              { label: '全部一致性', value: ALL_VALUE },
              { label: '一致', value: 'matched' },
              { label: '不一致', value: 'mismatched' },
              { label: '无法评审', value: 'unreviewable' },
              { label: '未执行', value: 'unrun' },
            ]}
          />
          <Tooltip title={selectedRowKeys.length ? `将执行已选 ${selectedRowKeys.length} 条案例` : '请先选择验证案例'}>
            <span><Button size="small" disabled={!selectedRowKeys.length || regressionActive} loading={regressionActive} onClick={executeBatch}>批量执行</Button></span>
          </Tooltip>
          <Tooltip title={selectedRowKeys.length ? `将删除已选 ${selectedRowKeys.length} 条案例，删除会二次确认` : '请先选择验证案例'}>
            <span><Button size="small" danger disabled={!selectedRowKeys.length} onClick={confirmBatchDelete}>批量删除</Button></span>
          </Tooltip>
          <Typography.Text type="secondary">已选 {selectedRowKeys.length} 条，当前显示 {filteredCases.length} / {cases.length} 条</Typography.Text>
        </Space>
      </Card>

      <Card className="compact-card" title="验证案例列表">
        {filteredCases.length || loading ? (
          <Table
            rowKey="caseId"
            className="compact-table"
            loading={loading}
            columns={columns}
            dataSource={filteredCases}
            size="small"
            rowSelection={{
              selectedRowKeys,
              onChange: setSelectedRowKeys,
              renderCell: (_checked, _record, _index, originNode) => (
                <span onClick={(event) => event.stopPropagation()}>{originNode}</span>
              ),
            }}
            onRow={(record) => ({
              onClick: () => setDetailCase(record),
            })}
            pagination={{
              current: casePage,
              pageSize: casePageSize,
              size: 'small',
              showSizeChanger: true,
              showTotal: (total) => `共 ${total} 条`,
              onChange: (nextPage, nextPageSize) => {
                setCasePage(nextPageSize !== casePageSize ? 1 : nextPage)
                setCasePageSize(nextPageSize)
              },
            }}
            scroll={{ x: 1460 }}
          />
        ) : cases.length ? (
          <Empty description="没有符合筛选条件的验证案例" />
        ) : (
          <Empty description={examples.length ? '暂无验证案例，可点击新增案例创建。' : '暂无验证集数据'} />
        )}
      </Card>

      <Drawer className="drawer-compact" title={detailCase?.caseName || '案例详情'} width="min(820px, 90vw)" open={pageActive && !!detailCase} mask={false} onClose={() => setDetailCase(null)}>
        {detailCase ? (
          <Space direction="vertical" size={12} style={{ width: '100%' }}>
            <Descriptions bordered column={1} size="small">
              <Descriptions.Item label="案例 ID">{detailCase.caseId}</Descriptions.Item>
              <Descriptions.Item label="因子名称">{detailCase.factorName}</Descriptions.Item>
              <Descriptions.Item label="期望结论">{conclusionLabel(detailCase.expectedConclusion)}</Descriptions.Item>
              <Descriptions.Item label="期望问题关键词">{detailCase.expectedIssuePatterns?.join('、') || '-'}</Descriptions.Item>
              <Descriptions.Item label="禁止问题关键词">{detailCase.forbiddenIssuePatterns?.join('、') || '-'}</Descriptions.Item>
              <Descriptions.Item label="最大问题数">{detailCase.maxIssueCount ?? '-'}</Descriptions.Item>
              <Descriptions.Item label="标签">{detailCase.tags?.length ? detailCase.tags.join('、') : '-'}</Descriptions.Item>
              <Descriptions.Item label="接口文档">{detailCase.apiDocIds?.join('、') || '-'}</Descriptions.Item>
              <Descriptions.Item label="业务规则">{detailCase.businessRuleIds?.join('、') || '-'}</Descriptions.Item>
              <Descriptions.Item label="备注">{detailCase.remark || '-'}</Descriptions.Item>
            </Descriptions>
            <Card className="compact-card" title="需求内容"><Typography.Paragraph style={{ whiteSpace: 'pre-wrap' }}>{detailCase.requirement}</Typography.Paragraph></Card>
            <Card className="compact-card" title="待评审内容"><Typography.Paragraph style={{ whiteSpace: 'pre-wrap' }}>{detailCase.reviewContent}</Typography.Paragraph></Card>
            <Card className="compact-card" title="最近执行结果">
              {detailRun ? (
                <Space direction="vertical" size={10} style={{ width: '100%' }}>
                  <Alert
                    showIcon
                    type={runAlertType(detailRun)}
                    message={`执行状态：${detailRun.status}`}
                    description={runDescription(detailRun)}
                  />
                  <Descriptions bordered column={2} size="small">
                    <Descriptions.Item label="Run ID">{detailRun.runId}</Descriptions.Item>
                    <Descriptions.Item label="开始时间">{formatDate(detailRun.startedAt)}</Descriptions.Item>
                    <Descriptions.Item label="结束时间">{formatDate(detailRun.finishedAt)}</Descriptions.Item>
                    <Descriptions.Item label="标准文件">{detailRun.standardFile || '-'}</Descriptions.Item>
                    <Descriptions.Item label="报告文件">{detailRun.reviewFile || '-'}</Descriptions.Item>
                    <Descriptions.Item label="结论匹配">{detailRun.conclusionMatched == null ? '-' : detailRun.conclusionMatched ? '一致' : '不一致'}</Descriptions.Item>
                    <Descriptions.Item label="问题匹配">{detailRun.issuesMatched == null ? '-' : detailRun.issuesMatched ? '一致' : '不一致'}</Descriptions.Item>
                  </Descriptions>
                  <Button size="small" icon={<EyeOutlined />} onClick={() => openRunResult(detailRun)}>
                    打开运行详情
                  </Button>
                </Space>
              ) : (
                <Empty image={Empty.PRESENTED_IMAGE_SIMPLE} description="该案例尚未执行验证" />
              )}
            </Card>
          </Space>
        ) : null}
      </Drawer>

      <Drawer
        title={editingCase ? '编辑验证案例' : '新增验证案例'}
        className="drawer-compact"
        width="min(820px, 90vw)"
        open={pageActive && editorOpen}
        mask={false}
        onClose={() => setEditorOpen(false)}
        extra={<Space><Button onClick={() => setEditorOpen(false)}>取消</Button><Button type="primary" onClick={saveCase}>保存</Button></Space>}
      >
        <Form form={form} layout="vertical">
          <Form.Item name="caseName" label="案例名称" rules={[{ required: true, message: '请输入案例名称' }]}>
            <Input />
          </Form.Item>
          <Form.Item name="factorName" label="因子名称" rules={[{ required: true, message: '请输入因子名称' }]}>
            <Input />
          </Form.Item>
          <Form.Item name="expectedConclusion" label="期望结论">
            <Select
              options={[
                { label: '通过', value: 'pass' },
                { label: '不通过', value: 'fail' },
                { label: '无法评审', value: 'unreviewable' },
              ]}
            />
          </Form.Item>
          <Form.Item name="expectedIssuePatternsText" label="期望问题关键词">
            <Input.TextArea rows={2} placeholder="可选，每行一个；评审报告需全部包含" />
          </Form.Item>
          <Form.Item name="forbiddenIssuePatternsText" label="禁止问题关键词">
            <Input.TextArea rows={2} placeholder="可选，每行一个；评审报告不得出现" />
          </Form.Item>
          <Form.Item name="maxIssueCount" label="最大问题数">
            <InputNumber min={0} precision={0} placeholder="可选" style={{ width: '100%' }} />
          </Form.Item>
          <Form.Item name="apiDocIdsText" label="关联接口文档" rules={[{ required: true, message: '请输入至少一个接口文档' }]}>
            <Input.TextArea rows={2} placeholder="每行一个，或用逗号分隔" />
          </Form.Item>
          <Form.Item name="businessRuleIdsText" label="业务规则 ID">
            <Input.TextArea rows={2} placeholder="可选，每行一个" />
          </Form.Item>
          <Form.Item name="requirement" label="需求内容" rules={[{ required: true, message: '请输入需求内容' }]}>
            <Input.TextArea rows={4} />
          </Form.Item>
          <Form.Item name="reviewContent" label="待评审内容" rules={[{ required: true, message: '请输入待评审内容' }]}>
            <Input.TextArea rows={6} />
          </Form.Item>
          <Form.Item name="tagsText" label="标签">
            <Input.TextArea rows={2} placeholder="可选，每行一个" />
          </Form.Item>
          <Form.Item name="remark" label="备注">
            <Input.TextArea rows={3} />
          </Form.Item>
        </Form>
      </Drawer>

      <Drawer className="drawer-compact" title="验证结果详情" width="min(860px, 90vw)" open={pageActive && resultOpen} mask={false} onClose={() => setResultOpen(false)}>
        {activeRun ? (
          <Space direction="vertical" size={12} style={{ width: '100%' }}>
            <Alert
              showIcon
              type={runAlertType(activeRun)}
              message={`执行状态：${activeRun.status}`}
              description={runDescription(activeRun)}
            />
            <Descriptions bordered column={1} size="small">
              <Descriptions.Item label="Run ID">{activeRun.runId}</Descriptions.Item>
              <Descriptions.Item label="案例 ID">{activeRun.caseId}</Descriptions.Item>
              <Descriptions.Item label="开始时间">{formatDate(activeRun.startedAt)}</Descriptions.Item>
              <Descriptions.Item label="结束时间">{formatDate(activeRun.finishedAt)}</Descriptions.Item>
              <Descriptions.Item label="标准文件">{activeRun.standardFile || '-'}</Descriptions.Item>
              <Descriptions.Item label="报告文件">{activeRun.reviewFile || '-'}</Descriptions.Item>
            </Descriptions>
            <Space wrap>
              <Button
                type={activeRunDocument === 'review' ? 'primary' : 'default'}
                disabled={!activeRun.reviewFile}
                onClick={() => setActiveRunDocument('review')}
              >
                查看评审结果
              </Button>
              <Button
                type={activeRunDocument === 'standard' ? 'primary' : 'default'}
                disabled={!activeRun.standardFile}
                onClick={() => setActiveRunDocument('standard')}
              >
                查看标准
              </Button>
            </Space>
            <Card className="compact-card" title={activeRunDocument === 'review' ? '评审结果' : '标准'}>
              {loadingRegressionFile === activeRunFileId ? (
                <Typography.Text type="secondary">正在读取文档...</Typography.Text>
              ) : (
                <MarkdownViewer
                  content={activeRunContent || activeRun.error || '暂无文档内容'}
                  fileName={activeRunFileId || (activeRunDocument === 'review' ? '评审结果.md' : '标准.md')}
                />
              )}
            </Card>
          </Space>
        ) : null}
      </Drawer>

      <Drawer
        className="drawer-compact"
        title={activeRegressionItem?.title || activeRegressionItem?.id || '回归结果详情'}
        width="min(900px, 92vw)"
        open={pageActive && !!activeRegressionItem}
        mask={false}
        onClose={() => setActiveRegressionItem(null)}
      >
        {activeRegressionItem ? (
          <Space direction="vertical" size={12} style={{ width: '100%' }}>
            <Alert
              showIcon
              type={activeRegressionItem.status === 'pass' ? 'success' : activeRegressionItem.status === 'running' ? 'info' : 'error'}
              message={`执行状态：${activeRegressionItem.status || '未知'}`}
              description={activeRegressionItem.expectedOk ? '实际结论与期望一致' : activeRegressionItem.status === 'running' ? '案例仍在执行' : '实际结论与期望不一致或执行失败'}
            />
            <Descriptions bordered column={1} size="small">
              <Descriptions.Item label="案例 ID">{activeRegressionItem.id || '-'}</Descriptions.Item>
              <Descriptions.Item label="期望结论">{activeRegressionItem.expected || '-'}</Descriptions.Item>
              <Descriptions.Item label="实际结论">{activeRegressionItem.verdict ? conclusionLabel(activeRegressionItem.verdict) : '-'}</Descriptions.Item>
              <Descriptions.Item label="标准文件">{activeRegressionItem.standardFileId || '-'}</Descriptions.Item>
              <Descriptions.Item label="报告文件">{activeRegressionItem.reviewFileId || '-'}</Descriptions.Item>
            </Descriptions>
            <Space wrap>
              <Button
                type={activeRegressionDocument === 'review' ? 'primary' : 'default'}
                disabled={!activeRegressionItem.raw && !activeRegressionItem.reportPreview && !activeRegressionItem.reviewFileId}
                onClick={() => setActiveRegressionDocument('review')}
              >
                查看评审结果
              </Button>
              <Button
                type={activeRegressionDocument === 'standard' ? 'primary' : 'default'}
                disabled={!activeRegressionItem.standardText && !activeRegressionItem.standardFileId}
                onClick={() => setActiveRegressionDocument('standard')}
              >
                查看标准
              </Button>
            </Space>
            <Card
              className="compact-card"
              title={activeRegressionDocument === 'review' ? '评审结果' : '标准'}
            >
              {loadingRegressionFile === activeRegressionFileId ? (
                <Typography.Text type="secondary">正在读取文档...</Typography.Text>
              ) : (
                <MarkdownViewer
                  content={activeRegressionContent || '暂无文档内容'}
                  fileName={activeRegressionFileId || (activeRegressionDocument === 'review' ? '评审结果.md' : '标准.md')}
                />
              )}
            </Card>
          </Space>
        ) : null}
      </Drawer>

      <Modal
        title="案例导入结果"
        open={pageActive && !!importResult}
        maskClosable={false}
        onCancel={() => setImportResult(null)}
        footer={<Button type="primary" onClick={() => setImportResult(null)}>关闭</Button>}
        width="min(900px, 90vw)"
      >
        {importResult ? (
          <Space direction="vertical" size={12} style={{ width: '100%' }}>
            <Alert
              showIcon
              type={importResult.failed ? 'warning' : 'success'}
              message={`文件 ${importResult.fileName}：共 ${importResult.totalRows} 条，成功 ${importResult.success} 条，失败 ${importResult.failed} 条`}
              description="案例名称为空时自动使用因子名称；分列填写的因子加工输入字段、加工逻辑和默认值会自动合并为待评审内容。"
            />
            {importResult.errors.length ? (
              <Table
                rowKey={(record) => `${record.rowNumber}-${record.caseId || record.factorName || ''}`}
                className="compact-table"
                size="small"
                pagination={false}
                dataSource={importResult.errors}
                columns={[
                  { title: '行号', dataIndex: 'rowNumber', width: 80 },
                  { title: '案例名称', dataIndex: 'caseName', ellipsis: true, render: (value?: string) => value || '-' },
                  { title: '因子名称', dataIndex: 'factorName', ellipsis: true, render: (value?: string) => value || '-' },
                  { title: '失败原因', dataIndex: 'error', ellipsis: true },
                ]}
              />
            ) : (
              <Typography.Text type="secondary">全部案例导入成功。</Typography.Text>
            )}
          </Space>
        ) : null}
      </Modal>

    </Space>
  )
}
