import { DeleteOutlined, EditOutlined, EyeOutlined, PlayCircleOutlined, PlusOutlined, ReloadOutlined, UploadOutlined } from '@ant-design/icons'
import {
  Alert,
  App as AntdApp,
  Button,
  Card,
  Empty,
  Form,
  Input,
  Select,
  Space,
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
import {
  ALL_VALUE,
  REGRESSION_TASK_STORAGE_KEY,
  RegressionTaskCard,
  RegressionResultDrawer,
  ValidationCaseDetailDrawer,
  ValidationCaseEditorDrawer,
  ValidationCaseWorkspace,
  ValidationImportResultModal,
  ValidationRunDrawer,
  ValidationStats,
  conclusionColor,
  conclusionLabel,
  formatDate,
  isTerminalRun,
  isTerminalTask,
  joinValues,
  normalizeConclusion,
  runMatched,
  selectRegressionTask,
  splitText,
  type RegressionDocumentType,
  type CaseFormValues,
} from '../features/validation-set'
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
  const { modal } = AntdApp.useApp()
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
    if (!pageActive) return
    let disposed = false

    async function restoreRegressionTask() {
      setRegressionRecovering(true)
      try {
        let storedTask: TaskRecord<RegressionTaskResult> | undefined
        const storedTaskId = window.localStorage.getItem(REGRESSION_TASK_STORAGE_KEY) || ''
        if (storedTaskId) {
          try {
            storedTask = await fetchTask(storedTaskId) as TaskRecord<RegressionTaskResult>
          } catch (caught) {
            if (!(caught instanceof AppError) || caught.status !== 404) throw caught
            window.localStorage.removeItem(REGRESSION_TASK_STORAGE_KEY)
          }
        }

        const task = selectRegressionTask(await fetchTasks(), storedTask, getClientId())

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
  }, [pageActive])

  useEffect(() => {
    if (!pageActive || !regressionTaskId) return
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
  }, [pageActive, regressionTaskId])

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
    modal.confirm({
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
    modal.confirm({
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
    modal.confirm({
      title: '确认终止本次回归验证？',
      content: '排队中的案例会被取消，正在运行的模型调用会被终止，已完成结果仍会保留。',
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
        const caseRunning = runningIds.includes(record.caseId) || Boolean(latestRun && !isTerminalRun(latestRun))
        return (
          <Space wrap onClick={(event) => event.stopPropagation()}>
            <Button
              type="link"
              icon={<PlayCircleOutlined />}
              disabled={regressionActive && !caseRunning}
              loading={caseRunning}
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

      <ValidationStats value={stats} />

      <RegressionTaskCard
        task={regressionTask}
        taskId={regressionTaskId}
        recovering={regressionRecovering}
        cancelling={regressionCancelling}
        active={regressionActive}
        onTerminate={terminateRegression}
        onRefresh={() => void refreshRegressionSnapshot()}
        onOpenResult={openRegressionResult}
      />

      {loadError ? (
        <Alert
          showIcon
          type="error"
          message="验证集加载失败"
          description={loadError}
          action={<Button size="small" onClick={loadAll}>重试</Button>}
        />
      ) : null}

      <ValidationCaseWorkspace
        query={query}
        conclusionFilter={conclusionFilter}
        matchFilter={matchFilter}
        selectedRowKeys={selectedRowKeys}
        filteredCases={filteredCases}
        totalCases={cases.length}
        sourceExampleCount={examples.length}
        loading={loading}
        regressionActive={regressionActive}
        columns={columns}
        currentPage={casePage}
        pageSize={casePageSize}
        onQueryChange={setQuery}
        onConclusionFilterChange={setConclusionFilter}
        onMatchFilterChange={setMatchFilter}
        onSelectionChange={setSelectedRowKeys}
        onBatchRun={() => void executeBatch()}
        onBatchDelete={confirmBatchDelete}
        onOpenCase={setDetailCase}
        onPageChange={(nextPage, nextPageSize) => {
          setCasePage(nextPageSize !== casePageSize ? 1 : nextPage)
          setCasePageSize(nextPageSize)
        }}
      />

      <ValidationCaseDetailDrawer
        active={pageActive}
        validationCase={detailCase}
        latestRun={detailRun}
        onClose={() => setDetailCase(null)}
        onOpenRun={openRunResult}
      />

      <ValidationCaseEditorDrawer
        active={pageActive}
        editingCase={editingCase}
        open={editorOpen}
        form={form}
        onClose={() => setEditorOpen(false)}
        onSave={() => void saveCase()}
      />

      <ValidationRunDrawer
        active={pageActive}
        open={resultOpen}
        run={activeRun}
        documentType={activeRunDocument}
        fileId={activeRunFileId}
        content={activeRunContent}
        loadingFile={loadingRegressionFile}
        onClose={() => setResultOpen(false)}
        onDocumentChange={setActiveRunDocument}
      />

      <RegressionResultDrawer
        active={pageActive}
        item={activeRegressionItem}
        documentType={activeRegressionDocument}
        fileId={activeRegressionFileId}
        content={activeRegressionContent}
        loadingFile={loadingRegressionFile}
        onClose={() => setActiveRegressionItem(null)}
        onDocumentChange={setActiveRegressionDocument}
      />

      <ValidationImportResultModal active={pageActive} result={importResult} onClose={() => setImportResult(null)} />

    </Space>
  )
}
