import { CopyOutlined, DeleteOutlined, EditOutlined, EyeOutlined, PlayCircleOutlined, PlusOutlined, ReloadOutlined, UploadOutlined } from '@ant-design/icons'
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
  Modal,
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
  batchRunValidationCases,
  createValidationCase,
  deleteValidationCase,
  fetchValidationCases,
  fetchValidationRuns,
  importValidationCases,
  runValidationCase,
  updateValidationCase,
  AppError,
  pollTask,
  runRegression,
} from '../api/reviewApi'
import { HeaderActions } from '../components/AppLayout'
import type {
  AppNotice,
  BatchValidationRun,
  CreateValidationCaseRequest,
  ExampleItem,
  ValidationCase,
  ValidationCaseImportResult,
  ValidationRun,
} from '../types/review'

interface ValidationSetProps {
  examples: ExampleItem[]
  onNotice: (notice: AppNotice) => void
  onCopyToSingle: (validationCase: ValidationCase) => void
}

type CaseFormValues = CreateValidationCaseRequest & {
  apiDocIdsText?: string
  businessRuleIdsText?: string
  tagsText?: string
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
  if (normalized === 'confirm') return 'warning'
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

function normalizeConclusion(value?: string) {
  const source = String(value || 'confirm').trim()
  const candidates: string[] = []
  if (/✅|\bpass\b|通过/.test(source) && !/不通过/.test(source)) candidates.push('pass')
  if (/❌|\bfail\b|不通过|不符合/.test(source)) candidates.push('fail')
  if (/⚠️|\bsuggestion\b|建议/.test(source)) candidates.push('suggestion')
  if (/待确认|\bconfirm\b|\bunknown\b/.test(source)) candidates.push('confirm')
  if (candidates.length) return [...new Set(candidates)].join('|')
  const normalized = source.toLowerCase()
  if (normalized === 'unknown') return 'confirm'
  if (normalized === 'skip') return 'suggestion'
  return normalized || 'confirm'
}

function conclusionLabel(value?: string) {
  const labels: Record<string, string> = {
    pass: '通过项',
    fail: '不符合项',
    confirm: '待确认项',
    suggestion: '建议项',
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

export function ValidationSet({ examples, onNotice, onCopyToSingle }: ValidationSetProps) {
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
  const [batchResult, setBatchResult] = useState<BatchValidationRun | null>(null)
  const [query, setQuery] = useState('')
  const [conclusionFilter, setConclusionFilter] = useState(ALL_VALUE)
  const [matchFilter, setMatchFilter] = useState(ALL_VALUE)
  const [examplesBackend, setExamplesBackend] = useState(false)

  function casesFromExamples(): ValidationCase[] {
    return examples.map((item) => ({
      caseId: item.id,
      caseName: item.title || item.id,
      factorName: item.factorName || '',
      requirement: item.requirement || '',
      reviewContent: item.reviewContent || item.draft || item.procLogic || '',
      expectedConclusion: item.expected,
      apiDocIds: item.apiNames || [],
      tags: item.kind ? [item.kind] : [],
      updatedAt: item.updatedAt ? new Date(item.updatedAt * 1000).toISOString() : undefined,
    }))
  }

  function openOriginalValidationWorkbench() {
    window.open('/legacy/', '_blank', 'noopener,noreferrer')
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
      if (caught instanceof AppError && [404, 410].includes(caught.status || 0)) {
        setExamplesBackend(true)
        setCases(casesFromExamples())
        setRuns([])
        return
      }
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

  const stats = useMemo(() => {
    const latest = cases.map((item) => latestRunByCase.get(item.caseId)).filter(Boolean) as ValidationRun[]
    const completed = latest.filter(isTerminalRun)
    const matched = completed.filter((run) => runMatched(run) === true).length
    return {
      total: cases.length,
      passed: latest.filter((run) => normalizeConclusion(run.actualConclusion) === 'pass').length,
      failed: latest.filter((run) => normalizeConclusion(run.actualConclusion) === 'fail').length,
      confirm: latest.filter((run) => normalizeConclusion(run.actualConclusion) === 'confirm').length,
      suggestion: latest.filter((run) => normalizeConclusion(run.actualConclusion) === 'suggestion').length,
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
      if (matchFilter === 'confirm' && normalizeConclusion(latestRun?.actualConclusion) !== 'confirm') return false
      return true
    })
  }, [cases, conclusionFilter, latestRunByCase, matchFilter, query])

  function openCreator() {
    if (examplesBackend) {
      openOriginalValidationWorkbench()
      return
    }
    setEditingCase(null)
    form.resetFields()
    form.setFieldsValue({ expectedConclusion: 'confirm' })
    setEditorOpen(true)
  }

  function openEditor(record: ValidationCase) {
    if (examplesBackend) {
      openOriginalValidationWorkbench()
      return
    }
    setEditingCase(record)
    form.setFieldsValue({
      caseName: record.caseName,
      factorName: record.factorName,
      requirement: record.requirement,
      reviewContent: record.reviewContent || '',
      expectedConclusion: normalizeConclusion(record.expectedConclusion),
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
    if (examplesBackend) {
      openOriginalValidationWorkbench()
      return false
    }
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
    if (examplesBackend) {
      openOriginalValidationWorkbench()
      return
    }
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
    setRunningIds((ids) => [...ids, record.caseId])
    try {
      if (examplesBackend) {
        const response = await runRegression({ exampleIds: [record.caseId], skipReviewer: true, skipStageValidation: true })
        await pollTask(response.taskId)
        onNotice({ type: 'success', message: '验证案例执行完成，可在原有功能台查看详细结果' })
        return
      }
      const run = await runValidationCase(record.caseId)
      setActiveRun(run)
      setResultOpen(true)
      onNotice({ type: 'success', message: '验证任务已进入全局队列' })
      await loadAll()
    } catch (caught) {
      const message = caught instanceof Error ? caught.message : '验证执行失败'
      onNotice({ type: 'error', message })
    } finally {
      setRunningIds((ids) => ids.filter((id) => id !== record.caseId))
    }
  }

  function confirmBatchDelete() {
    if (examplesBackend) {
      openOriginalValidationWorkbench()
      return
    }
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
    setRunningIds(ids)
    try {
      if (examplesBackend) {
        const response = await runRegression({ exampleIds: ids, skipReviewer: true, skipStageValidation: true })
        await pollTask(response.taskId)
        onNotice({ type: 'success', message: `已完成 ${ids.length} 条回归验证，可在原有功能台查看详细结果` })
        return
      }
      const result = await batchRunValidationCases(ids)
      setBatchResult(result)
      onNotice({
        type: result.failed ? 'warning' : 'success',
        message: `批量验证已提交：入队 ${result.success}，失败 ${result.failed}`,
      })
      await loadAll()
    } catch (caught) {
      const message = caught instanceof Error ? caught.message : '批量执行失败'
      onNotice({ type: 'error', message })
    } finally {
      setRunningIds([])
    }
  }

  function openRunResult(run?: ValidationRun) {
    if (!run) return
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
      width: 380,
      render: (_value, record) => {
        const latestRun = latestRunByCase.get(record.caseId)
        return (
          <Space wrap onClick={(event) => event.stopPropagation()}>
            <Button type="link" icon={<CopyOutlined />} onClick={() => onCopyToSingle(record)}>复制到单因子</Button>
            <Button type="link" icon={<PlayCircleOutlined />} loading={runningIds.includes(record.caseId)} onClick={() => executeOne(record)}>
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
      <HeaderActions>
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
        <Col xs={24} sm={12} xl={4}><Card className="compact-card"><Statistic title="待确认项" value={stats.confirm} /></Card></Col>
        <Col xs={24} sm={12} xl={4}><Card className="compact-card"><Statistic title="一致率" value={stats.agreementRate ?? '暂无'} suffix={stats.agreementRate == null ? undefined : '%'} precision={1} /></Card></Col>
        <Col xs={24} sm={12} xl={4}><Card className="compact-card"><CompactDateStat title="最近执行时间" value={stats.recent} /></Card></Col>
      </Row>

      {examplesBackend ? (
        <Alert
          showIcon
          type="info"
          message="当前后端使用 examples 验证集接口"
          description="新页面支持查看、复制到单因子和提交回归验证；新增、编辑、删除和结果明细继续在原有功能台使用。"
          action={<Button size="small" onClick={openOriginalValidationWorkbench}>打开原有功能台</Button>}
        />
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
              { label: '通过项', value: 'pass' },
              { label: '不符合项', value: 'fail' },
              { label: '待确认项', value: 'confirm' },
              { label: '建议项', value: 'suggestion' },
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
              { label: '待确认项', value: 'confirm' },
              { label: '未执行', value: 'unrun' },
            ]}
          />
          <Tooltip title={selectedRowKeys.length ? `将执行已选 ${selectedRowKeys.length} 条案例` : '请先选择验证案例'}>
            <span><Button size="small" disabled={!selectedRowKeys.length} loading={!!runningIds.length} onClick={executeBatch}>批量执行</Button></span>
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
            pagination={{ pageSize: 10, size: 'small', showSizeChanger: true }}
            scroll={{ x: 1460 }}
          />
        ) : cases.length ? (
          <Empty description="没有符合筛选条件的验证案例" />
        ) : (
          <Empty description={examples.length ? '暂无验证案例，可点击新增案例创建。' : '暂无验证集数据'} />
        )}
      </Card>

      <Drawer className="drawer-compact" title={detailCase?.caseName || '案例详情'} width="min(820px, 90vw)" open={!!detailCase} onClose={() => setDetailCase(null)}>
        {detailCase ? (
          <Space direction="vertical" size={12} style={{ width: '100%' }}>
            <Descriptions bordered column={1} size="small">
              <Descriptions.Item label="案例 ID">{detailCase.caseId}</Descriptions.Item>
              <Descriptions.Item label="因子名称">{detailCase.factorName}</Descriptions.Item>
              <Descriptions.Item label="期望结论">{conclusionLabel(detailCase.expectedConclusion)}</Descriptions.Item>
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
        open={editorOpen}
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
                { label: '通过项', value: 'pass' },
                { label: '不符合项', value: 'fail' },
                { label: '待确认项', value: 'confirm' },
                { label: '建议项', value: 'suggestion' },
              ]}
            />
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

      <Drawer className="drawer-compact" title="验证结果详情" width="min(860px, 90vw)" open={resultOpen} onClose={() => setResultOpen(false)}>
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
              <Tooltip title={activeRun.standardFile ? '打开本次运行生成的准出文档' : '本次运行未返回准出文档'}>
                <span>
                  <Button disabled={!activeRun.standardFile} href={activeRun.standardFile ? `/outputs/${encodeURIComponent(activeRun.standardFile)}` : undefined} target="_blank">查看准出文档</Button>
                </span>
              </Tooltip>
              <Tooltip title={activeRun.reviewFile ? '打开本次运行生成的评审报告' : '本次运行未返回评审报告'}>
                <span>
                  <Button disabled={!activeRun.reviewFile} href={activeRun.reviewFile ? `/outputs/${encodeURIComponent(activeRun.reviewFile)}` : undefined} target="_blank">查看评审结果</Button>
                </span>
              </Tooltip>
            </Space>
          </Space>
        ) : null}
      </Drawer>

      <Modal
        title="案例导入结果"
        open={!!importResult}
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

      <Modal
        title="批量验证结果"
        open={!!batchResult}
        onCancel={() => setBatchResult(null)}
        footer={<Button type="primary" onClick={() => setBatchResult(null)}>关闭</Button>}
        width="min(900px, 90vw)"
      >
        {batchResult ? (
          <Space direction="vertical" size={12} style={{ width: '100%' }}>
            <Alert
              showIcon
              type={batchResult.failed || batchResult.mismatched ? 'warning' : 'success'}
              message={`总数 ${batchResult.total}，成功 ${batchResult.success}，失败 ${batchResult.failed}，待确认 ${batchResult.confirm ?? 0}，不符合预期 ${batchResult.mismatched}`}
            />
            <Table
              rowKey="id"
              className="compact-table"
              size="small"
              pagination={false}
              dataSource={batchResult.items}
              columns={[
                { title: '案例 ID', dataIndex: 'id', ellipsis: true },
                { title: '状态', render: (_value, record) => record.success ? <Tag color="success">成功</Tag> : <Tag color="error">失败</Tag> },
                { title: '实际结论', render: (_value, record) => record.run?.actualConclusion ? conclusionLabel(record.run.actualConclusion) : '-' },
                {
                  title: '是否符合',
                  render: (_value, record) => {
                    const matched = runMatched(record.run)
                    if (matched === true) return '符合'
                    if (matched === false) return '不符合'
                    return record.run ? '执行中' : '-'
                  },
                },
                {
                  title: '结果',
                  width: 120,
                  render: (_value, record) => (
                    <Button type="link" size="small" icon={<EyeOutlined />} disabled={!record.run} onClick={() => openRunResult(record.run)}>
                      查看结果
                    </Button>
                  ),
                },
                { title: '错误明细', dataIndex: 'error', ellipsis: true, render: (value?: string) => value || '-' },
              ]}
            />
          </Space>
        ) : null}
      </Modal>
    </Space>
  )
}
