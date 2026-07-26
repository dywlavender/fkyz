import { DeleteOutlined, DownloadOutlined, DownOutlined, EyeOutlined, MoreOutlined, PauseCircleOutlined, PlayCircleOutlined, PlusOutlined, ReloadOutlined } from '@ant-design/icons'
import {
  Alert,
  Button,
  Card,
  Col,
  DatePicker,
  Dropdown,
  Drawer,
  Empty,
  Form,
  Input,
  InputNumber,
  Popconfirm,
  Radio,
  Row,
  Select,
  Space,
  Steps,
  Statistic,
  Switch,
  Table,
  Tag,
  TimePicker,
  Tooltip,
  Typography,
  Upload,
} from 'antd'
import type { ColumnsType } from 'antd/es/table'
import dayjs from 'dayjs'
import { useEffect, useMemo, useState } from 'react'

import {
  createScheduledTask,
  deleteScheduledTask,
  disableScheduledTask,
  enableScheduledTask,
  fetchScheduledTask,
  fetchScheduledTasks,
  previewScheduledTaskImport,
  runScheduledTaskNow,
  updateScheduledTask,
  AppError,
} from '../api/reviewApi'
import { HeaderActions, usePageActive } from '../components/AppLayout'
import { downloadScheduledTaskImportTemplate } from '../utils/csvTemplates'
import type {
  AppNotice,
  CreateScheduledTaskRequest,
  ImportPreviewResult,
  ScheduledTask,
  ScheduledTaskItem,
  UpdateScheduledTaskRequest
} from '../types/review'
import { ScheduledTaskDetail } from './ScheduledTaskDetail'

interface ScheduledTasksProps {
  onNotice: (notice: AppNotice) => void
}

interface TaskFormValues {
  taskName: string
  enabled: boolean
  mode: CreateScheduledTaskRequest['mode']
  runtimeMode: CreateScheduledTaskRequest['runtimeMode']
  failurePolicy: CreateScheduledTaskRequest['failurePolicy']
  scheduleType: CreateScheduledTaskRequest['scheduleType']
  runDate?: dayjs.Dayjs
  runTime?: dayjs.Dayjs
  weekday?: string
  monthDay?: number
  cron?: string
  standardFile?: string
  ignoreErrorRows?: boolean
}

const DEFAULT_FORM_VALUES: TaskFormValues = {
  taskName: '',
  enabled: true,
  mode: 'phase1_phase2',
  runtimeMode: 'claude',
  failurePolicy: 'continue',
  scheduleType: 'daily',
  runTime: dayjs('02:00', 'HH:mm'),
  weekday: '1',
  monthDay: 1,
  cron: '0 2 * * *',
  standardFile: '',
  ignoreErrorRows: false,
}

function formatDate(value?: string) {
  if (!value) return '-'
  return new Date(value).toLocaleString('zh-CN', { hour12: false })
}

function scheduleLabel(task: ScheduledTask) {
  const timeText = task.runTime || '--:--'
  if (task.scheduleType === 'once') return `单次 ${task.runDate || '-'} ${timeText}`
  if (task.scheduleType === 'daily') return `每日 ${timeText}`
  if (task.scheduleType === 'weekly') return `每周 ${task.weekday || '-'} ${timeText}`
  if (task.scheduleType === 'monthly') return `每月 ${task.monthDay || '-'} 日 ${timeText}`
  return `Cron ${task.cron || '-'}`
}

const SCHEDULE_TYPE_LABELS: Record<TaskFormValues['scheduleType'], string> = {
  once: '单次',
  daily: '每日',
  weekly: '每周',
  monthly: '每月',
  cron: 'Cron',
}

const TASK_MODE_LABELS: Record<TaskFormValues['mode'], string> = {
  phase1_only: '仅执行 Phase 1',
  phase1_phase2: '执行 Phase 1 + Phase 2',
  phase2_only: '仅执行 Phase 2',
}

function statusColor(status?: string) {
  const normalized = String(status || '').toLowerCase()
  if (normalized === 'success') return 'success'
  if (normalized === 'partial_success') return 'processing'
  if (normalized === 'failed') return 'error'
  if (normalized === 'running' || normalized === 'pending') return 'warning'
  return 'default'
}

export function ScheduledTasks({ onNotice }: ScheduledTasksProps) {
  const pageActive = usePageActive()
  const [form] = Form.useForm<TaskFormValues>()
  const [tasks, setTasks] = useState<ScheduledTask[]>([])
  const [loading, setLoading] = useState(false)
  const [creatorOpen, setCreatorOpen] = useState(false)
  const [creatorStep, setCreatorStep] = useState(0)
  const [preview, setPreview] = useState<ImportPreviewResult | null>(null)
  const [selectedFile, setSelectedFile] = useState<File | null>(null)
  const [detailTask, setDetailTask] = useState<ScheduledTask | null>(null)
  const [detailOpen, setDetailOpen] = useState(false)
  const [editingTaskId, setEditingTaskId] = useState<string | null>(null)
  const [openActionTaskId, setOpenActionTaskId] = useState<string | null>(null)
  const [actionLoadingIds, setActionLoadingIds] = useState<string[]>([])
  const [backendUnavailable, setBackendUnavailable] = useState(false)

  async function loadTasks() {
    setLoading(true)
    try {
      const data = await fetchScheduledTasks()
      setTasks(data)
      setBackendUnavailable(false)
    } catch (caught) {
      if (caught instanceof AppError && [404, 410].includes(caught.status || 0)) {
        setBackendUnavailable(true)
        setTasks([])
        return
      }
      const message = caught instanceof Error ? caught.message : '加载定时任务失败'
      onNotice({ type: 'error', message })
    } finally {
      setLoading(false)
    }
  }

  useEffect(() => {
    form.setFieldsValue(DEFAULT_FORM_VALUES)
    void loadTasks()
  }, [])

  useEffect(() => {
    if (pageActive && preview) setCreatorOpen(true)
  }, [pageActive])

  const previewColumns: ColumnsType<ScheduledTaskItem> = [
    { title: '因子名称', dataIndex: 'factorName' },
    { title: '需求', dataIndex: 'requirement', ellipsis: true },
    { title: '因子加工输入字段', dataIndex: 'paramFields', ellipsis: true, render: (value?: string) => value || '-' },
    { title: '加工逻辑', dataIndex: 'procLogic', ellipsis: true, render: (value?: string) => value || '-' },
    { title: '待评审内容', dataIndex: 'reviewContent', ellipsis: true, render: (value?: string) => value || '-' },
    { title: 'apiDocIds', dataIndex: 'apiDocIds', ellipsis: true, render: (value?: string[]) => value?.join('、') || '-' },
    { title: 'standardFile', dataIndex: 'standardFile', ellipsis: true, render: (value?: string) => value || '-' },
  ]

  const todayRunCount = useMemo(() => {
    const today = new Date()
    return tasks.filter((task) => {
      if (!task.lastRunAt) return false
      const date = new Date(task.lastRunAt)
      return date.getFullYear() === today.getFullYear()
        && date.getMonth() === today.getMonth()
        && date.getDate() === today.getDate()
    }).length
  }, [tasks])

  const taskMetrics = useMemo(() => ({
    total: tasks.length,
    enabled: tasks.filter((task) => task.enabled).length,
    recentFailed: tasks.filter((task) => String(task.lastRunStatus || '').toLowerCase() === 'failed').length,
    todayRuns: todayRunCount,
  }), [tasks, todayRunCount])

  function setActionLoading(taskId: string, loadingNext: boolean) {
    setActionLoadingIds((current) => {
      if (loadingNext) return current.includes(taskId) ? current : [...current, taskId]
      return current.filter((id) => id !== taskId)
    })
  }

  const taskColumns: ColumnsType<ScheduledTask> = [
    { title: '任务名称', dataIndex: 'taskName' },
    {
      title: '状态',
      dataIndex: 'enabled',
      width: 100,
      render: (value: boolean) => <Tag color={value ? 'success' : 'default'}>{value ? '启用' : '停用'}</Tag>,
    },
    {
      title: '执行周期',
      render: (_value, record) => scheduleLabel(record),
    },
    {
      title: '下次执行时间',
      dataIndex: 'nextRunAt',
      render: (value?: string) => formatDate(value),
    },
    {
      title: '因子数量',
      dataIndex: 'itemCount',
      width: 100,
      render: (value: number | undefined, record) => value ?? record.items.length,
    },
    {
      title: '最近执行结果',
      dataIndex: 'lastRunStatus',
      width: 140,
      render: (value?: string | null) => value ? <Tag color={statusColor(value)}>{value}</Tag> : '-',
    },
    {
      title: '最近执行时间',
      dataIndex: 'lastRunAt',
      render: (value?: string) => formatDate(value),
    },
    {
      title: '操作',
      width: 96,
      fixed: 'right',
      render: (_value, record) => {
        const loadingAction = actionLoadingIds.includes(record.taskId)
        return (
          <Dropdown
            trigger={['click']}
            open={openActionTaskId === record.taskId}
            onOpenChange={(open) => setOpenActionTaskId(open ? record.taskId : null)}
            destroyOnHidden
            dropdownRender={() => (
              <Card size="small" style={{ width: 168 }}>
                <Space direction="vertical" size={2} style={{ width: '100%' }}>
                  <Button block type="text" size="small" icon={<EyeOutlined />} onClick={() => void openDetail(record.taskId)}>查看</Button>
                  <Button block type="text" size="small" onClick={() => void openEditor(record.taskId)}>编辑</Button>
                  <Button
                    block
                    type="text"
                    size="small"
                    icon={record.enabled ? <PauseCircleOutlined /> : <PlayCircleOutlined />}
                    loading={loadingAction}
                    onClick={() => void toggleTask(record)}
                  >
                    {record.enabled ? '停用' : '启用'}
                  </Button>
                  <Button
                    block
                    type="text"
                    size="small"
                    icon={<ReloadOutlined />}
                    loading={loadingAction}
                    onClick={() => void handleRunNow(record.taskId)}
                  >
                    立即执行
                  </Button>
                  <Popconfirm
                    title="确认删除任务？"
                    description="删除后任务配置与执行记录都会移除。"
                    okText="删除"
                    cancelText="取消"
                    okButtonProps={{ danger: true }}
                    onConfirm={() => void handleDelete(record.taskId)}
                  >
                    <Button block danger type="text" size="small" icon={<DeleteOutlined />}>删除</Button>
                  </Popconfirm>
                </Space>
              </Card>
            )}
          >
            <Button size="small" icon={<MoreOutlined />} loading={loadingAction} onClick={(event) => event.stopPropagation()}>
              操作 <DownOutlined />
            </Button>
          </Dropdown>
        )
      },
    },
  ]

  const stepItems = [
    { title: '上传文件' },
    { title: '预览校验' },
    { title: '配置计划' },
    { title: '确认创建' },
  ]

  async function handlePreviewUpload(file: File) {
    setSelectedFile(file)
    const values = form.getFieldsValue()
    try {
      const result = await previewScheduledTaskImport(file, {
        mode: 'phase1_phase2',
        standardFile: values.standardFile,
      })
      setPreview(result)
      setCreatorStep(1)
      onNotice({ type: 'success', message: `预览完成，有效 ${result.validRows} 行` })
    } catch (caught) {
      const message = caught instanceof Error ? caught.message : '导入预览失败'
      onNotice({ type: 'error', message })
    }
    return false
  }

  function openCreator() {
    form.setFieldsValue(DEFAULT_FORM_VALUES)
    setPreview(null)
    setSelectedFile(null)
    setCreatorStep(0)
    setEditingTaskId(null)
    setCreatorOpen(true)
  }

  async function openDetail(taskId: string) {
    setOpenActionTaskId(null)
    try {
      const task = await fetchScheduledTask(taskId)
      setDetailTask(task)
      setDetailOpen(true)
    } catch (caught) {
      const message = caught instanceof Error ? caught.message : '加载任务详情失败'
      onNotice({ type: 'error', message })
    }
  }

  async function openEditor(taskId: string) {
    setOpenActionTaskId(null)
    try {
      const task = await fetchScheduledTask(taskId)
      setEditingTaskId(taskId)
      setPreview({
        fileName: 'existing-items',
        totalRows: task.items.length,
        validRows: task.items.length,
        errorRows: 0,
        items: task.items,
        errors: [],
        columns: [],
      })
      setSelectedFile(null)
      setCreatorStep(2)
      form.setFieldsValue({
        taskName: task.taskName,
        enabled: task.enabled,
        mode: 'phase1_phase2',
        runtimeMode: 'claude',
        failurePolicy: task.failurePolicy,
        scheduleType: task.scheduleType,
        runDate: task.runDate ? dayjs(task.runDate, 'YYYY-MM-DD') : undefined,
        runTime: task.runTime ? dayjs(task.runTime, 'HH:mm') : undefined,
        weekday: task.weekday || '1',
        monthDay: task.monthDay ? Number(task.monthDay) : 1,
        cron: task.cron || '',
        standardFile: task.standardFile || '',
        ignoreErrorRows: false,
      })
      setCreatorOpen(true)
    } catch (caught) {
      const message = caught instanceof Error ? caught.message : '加载任务失败'
      onNotice({ type: 'error', message })
    }
  }

  async function toggleTask(task: ScheduledTask) {
    setOpenActionTaskId(null)
    setActionLoading(task.taskId, true)
    try {
      if (task.enabled) {
        await disableScheduledTask(task.taskId)
        onNotice({ type: 'success', message: '任务已停用' })
      } else {
        await enableScheduledTask(task.taskId)
        onNotice({ type: 'success', message: '任务已启用' })
      }
      await loadTasks()
      if (detailTask?.taskId === task.taskId) {
        const nextDetail = await fetchScheduledTask(task.taskId)
        setDetailTask(nextDetail)
      }
    } catch (caught) {
      const message = caught instanceof Error ? caught.message : '更新任务状态失败'
      onNotice({ type: 'error', message })
    } finally {
      setActionLoading(task.taskId, false)
    }
  }

  async function handleDelete(taskId: string) {
    setOpenActionTaskId(null)
    setActionLoading(taskId, true)
    try {
      await deleteScheduledTask(taskId)
      onNotice({ type: 'success', message: '任务已删除' })
      if (detailTask?.taskId === taskId) {
        setDetailOpen(false)
        setDetailTask(null)
      }
      await loadTasks()
    } catch (caught) {
      const message = caught instanceof Error ? caught.message : '删除任务失败'
      onNotice({ type: 'error', message })
    } finally {
      setActionLoading(taskId, false)
    }
  }

  async function handleRunNow(taskId: string) {
    setOpenActionTaskId(null)
    setActionLoading(taskId, true)
    try {
      const run = await runScheduledTaskNow(taskId)
      onNotice({ type: 'success', message: `任务已提交执行${run.runId ? `：${run.runId}` : ''}` })
      await loadTasks()
      const nextDetail = await fetchScheduledTask(taskId)
      setDetailTask(nextDetail)
      setDetailOpen(true)
    } catch (caught) {
      const message = caught instanceof Error ? caught.message : '立即执行失败'
      onNotice({ type: 'error', message })
    } finally {
      setActionLoading(taskId, false)
    }
  }

  const watchedScheduleType = Form.useWatch('scheduleType', { form, preserve: true })
  const watchedIgnoreErrorRows = Form.useWatch('ignoreErrorRows', { form, preserve: true })

  const canCreate = useMemo(() => {
    if (!preview) return false
    if (!preview.items.length) return false
    return preview.errorRows === 0 || Boolean(watchedIgnoreErrorRows)
  }, [preview, watchedIgnoreErrorRows])

  async function handleCreate() {
    try {
      await form.validateFields()
      const values = form.getFieldsValue(true)
      if (!preview) {
        onNotice({ type: 'warning', message: '请先上传并预览文件' })
        return
      }
      if (preview.errorRows > 0 && !values.ignoreErrorRows) {
        onNotice({ type: 'warning', message: '存在错误行，如需继续请勾选忽略错误行' })
        return
      }
      const payload: UpdateScheduledTaskRequest = {
        taskName: values.taskName,
        enabled: values.enabled,
        scheduleType: values.scheduleType,
        runDate: values.runDate?.format('YYYY-MM-DD'),
        runTime: values.runTime?.format('HH:mm'),
        weekday: values.weekday,
        monthDay: values.monthDay ? String(values.monthDay) : undefined,
        cron: values.cron,
        mode: 'phase1_phase2',
        runtimeMode: 'claude',
        failurePolicy: values.failurePolicy,
        standardFile: values.standardFile,
        items: preview.items,
      }
      if (editingTaskId) {
        await updateScheduledTask(editingTaskId, payload)
        onNotice({ type: 'success', message: '定时任务更新成功' })
      } else {
        await createScheduledTask(payload)
        onNotice({ type: 'success', message: '定时任务创建成功' })
      }
      setCreatorOpen(false)
      await loadTasks()
    } catch (caught) {
      const message = caught instanceof Error ? caught.message : '创建任务失败'
      onNotice({ type: 'error', message })
    }
  }

  async function handleNextCreatorStep() {
    if (creatorStep === 2) {
      try {
        await form.validateFields()
      } catch {
        return
      }
    }
    setCreatorStep((value) => Math.min(3, value + 1))
  }

  function renderScheduleFields(scheduleType?: string) {
    return (
      <>
        <Form.Item name="scheduleType" label="执行周期" rules={[{ required: true, message: '请选择执行周期' }]}>
          <Select
            options={[
              { value: 'once', label: '单次' },
              { value: 'daily', label: '每日' },
              { value: 'weekly', label: '每周' },
              { value: 'monthly', label: '每月' },
              { value: 'cron', label: 'Cron' },
            ]}
          />
        </Form.Item>
        {scheduleType === 'once' ? (
          <Form.Item name="runDate" label="执行日期" rules={[{ required: true, message: '请选择执行日期' }]}>
            <DatePicker style={{ width: '100%' }} />
          </Form.Item>
        ) : null}
        {(scheduleType === 'once' || scheduleType === 'daily' || scheduleType === 'weekly' || scheduleType === 'monthly') ? (
          <Form.Item name="runTime" label="执行时间" rules={[{ required: true, message: '请选择执行时间' }]}>
            <TimePicker style={{ width: '100%' }} format="HH:mm" />
          </Form.Item>
        ) : null}
        {scheduleType === 'weekly' ? (
          <Form.Item name="weekday" label="星期" rules={[{ required: true, message: '请选择星期' }]}>
            <Select
              options={[
                { value: '1', label: '周一' },
                { value: '2', label: '周二' },
                { value: '3', label: '周三' },
                { value: '4', label: '周四' },
                { value: '5', label: '周五' },
                { value: '6', label: '周六' },
                { value: '7', label: '周日' },
              ]}
            />
          </Form.Item>
        ) : null}
        {scheduleType === 'monthly' ? (
          <Form.Item name="monthDay" label="每月日期" rules={[{ required: true, message: '请输入每月日期' }]}>
            <InputNumber min={1} max={31} style={{ width: '100%' }} />
          </Form.Item>
        ) : null}
        {scheduleType === 'cron' ? (
          <Form.Item name="cron" label="Cron 表达式" rules={[{ required: true, message: '请输入 Cron 表达式' }]}>
            <Input placeholder="例如：0 2 * * *" />
          </Form.Item>
        ) : null}
      </>
    )
  }

  return (
    <Space className="app-page compact-form" direction="vertical" size={12} style={{ width: '100%' }}>
      <HeaderActions page="scheduled-tasks">
        <Space className="app-header-action-space" size={8} wrap>
          <Button icon={<ReloadOutlined />} loading={loading} onClick={loadTasks}>刷新</Button>
          <Button icon={<DownloadOutlined />} onClick={downloadScheduledTaskImportTemplate}>下载模板</Button>
          <Tooltip title={backendUnavailable ? '当前后端尚未启用定时任务接口，页面和代码已保留' : undefined}>
            <span><Button type="primary" icon={<PlusOutlined />} disabled={backendUnavailable} onClick={openCreator}>新建任务</Button></span>
          </Tooltip>
        </Space>
      </HeaderActions>

      {backendUnavailable ? (
        <Alert
          showIcon
          type="info"
          message="定时任务功能已保留，当前后端暂未启用"
          description="页面、类型、API 和组件均未删除。后端恢复 scheduled-tasks 接口后可直接继续对接。"
        />
      ) : null}

      <Row gutter={[12, 12]}>
        <Col xs={12} md={6}><Card className="compact-card"><Statistic title="任务总数" value={taskMetrics.total} /></Card></Col>
        <Col xs={12} md={6}><Card className="compact-card"><Statistic title="启用中" value={taskMetrics.enabled} /></Card></Col>
        <Col xs={12} md={6}><Card className="compact-card"><Statistic title="最近失败" value={taskMetrics.recentFailed} /></Card></Col>
        <Col xs={12} md={6}><Card className="compact-card"><Statistic title="今日执行" value={taskMetrics.todayRuns} /></Card></Col>
      </Row>

      <Card
        className="compact-card table-console-card"
        title="任务列表"
        extra={<Typography.Text type="secondary">点击整行查看任务详情</Typography.Text>}
      >
        <Table
          rowKey="taskId"
          className="compact-table"
          size="small"
          loading={loading}
          dataSource={tasks}
          columns={taskColumns}
          locale={{ emptyText: <Empty image={Empty.PRESENTED_IMAGE_SIMPLE} description="暂无定时任务，可新建任务导入因子文件。" /> }}
          onRow={(record) => ({
            onClick: () => void openDetail(record.taskId),
          })}
          pagination={{ pageSize: 10, size: 'small', showSizeChanger: true }}
          scroll={{ x: 1400 }}
        />
      </Card>

      <Drawer
        className="drawer-compact"
        title={editingTaskId ? '编辑定时任务' : '新建定时任务'}
        width="min(980px, 90vw)"
        open={pageActive && creatorOpen}
        mask={false}
        onClose={() => setCreatorOpen(false)}
        extra={
          <Space>
            <Button onClick={() => setCreatorOpen(false)}>取消</Button>
              <Button disabled={creatorStep <= 0} onClick={() => setCreatorStep((value) => value - 1)}>上一步</Button>
              {creatorStep < 3 ? (
                <Button
                  type="primary"
                  onClick={() => void handleNextCreatorStep()}
                  disabled={(creatorStep === 0 && !preview) || (creatorStep === 1 && !preview)}
                >
                  下一步
                </Button>
              ) : (
                <Button type="primary" disabled={!canCreate} onClick={handleCreate}>{editingTaskId ? '保存修改' : '确认创建'}</Button>
              )}
            </Space>
          }
      >
        <Space direction="vertical" size={12} style={{ width: '100%' }}>
          <div className="flow-steps-compact">
            <Steps current={creatorStep} items={stepItems} />
          </div>
          <Form form={form} layout="vertical" initialValues={DEFAULT_FORM_VALUES}>
            {creatorStep === 0 ? (
              <Card className="compact-card" title="Step 1：上传文件">
                {editingTaskId ? (
                  <Alert showIcon type="info" message="编辑模式沿用当前任务因子列表，如需更换文件请新建任务。" />
                ) : (
                  <Upload beforeUpload={handlePreviewUpload} maxCount={1} accept=".csv,.xlsx,.xls" showUploadList>
                    <Button icon={<PlusOutlined />}>上传 CSV / Excel</Button>
                  </Upload>
                )}
                <Typography.Paragraph type="secondary" style={{ marginTop: 8 }}>
                  支持字段：factorName、requirement、因子加工输入字段、procLogic、reviewContent、defaultVal、apiDocIds、businessRuleIds、standardFile、remark
                </Typography.Paragraph>
              </Card>
            ) : null}

            {creatorStep === 1 ? (
              <Card className="compact-card" title="Step 2：预览和校验因子">
                {preview ? (
                  <Space direction="vertical" size={12} style={{ width: '100%' }}>
                    <Alert
                      showIcon
                      type={preview.errorRows > 0 ? 'warning' : 'success'}
                      message={`总行数 ${preview.totalRows}，有效行数 ${preview.validRows}，错误行数 ${preview.errorRows}`}
                      description={preview.errorRows > 0 ? '存在错误行时，默认不允许创建；如需继续请勾选“忽略错误行”。' : '校验通过，可继续配置计划。'}
                    />
                    {preview.errors.length ? (
                      <Table
                        rowKey={(record) => `${record.rowNumber}-${record.error}`}
                        className="compact-table"
                        size="small"
                        dataSource={preview.errors}
                        pagination={false}
                        columns={[
                          { title: '行号', dataIndex: 'rowNumber', width: 100 },
                          { title: '因子名称', dataIndex: 'factorName', render: (value?: string) => value || '-' },
                          { title: '错误原因', dataIndex: 'error' },
                        ]}
                      />
                    ) : null}
                    <Table rowKey={(record) => `${record.factorName}-${record.requirement}`} className="compact-table" size="small" dataSource={preview.items} columns={previewColumns} scroll={{ x: 1000 }} />
                    <Form.Item name="ignoreErrorRows" valuePropName="checked">
                      <Switch checkedChildren="忽略错误行" unCheckedChildren="不忽略错误行" />
                    </Form.Item>
                  </Space>
                ) : (
                  <Alert type="info" showIcon message="请先上传文件" />
                )}
              </Card>
            ) : null}

            {creatorStep === 2 ? (
              <Card className="compact-card" title="Step 3：配置执行计划">
                <Form.Item name="taskName" label="任务名称" rules={[{ required: true, message: '请输入任务名称' }]}>
                  <Input placeholder="例如：每日加工逻辑评审" />
                </Form.Item>
                <Form.Item name="enabled" label="创建后启用" valuePropName="checked">
                  <Switch />
                </Form.Item>
                <Form.Item name="failurePolicy" label="失败策略" rules={[{ required: true, message: '请选择失败策略' }]}>
                  <Radio.Group
                    options={[
                      { value: 'continue', label: '失败继续' },
                      { value: 'stop', label: '失败停止' },
                    ]}
                  />
                </Form.Item>
                {renderScheduleFields(watchedScheduleType)}
              </Card>
            ) : null}

            {creatorStep === 3 ? (
              <Card className="compact-card" title="Step 4：确认创建">
                <Space direction="vertical" size={12} style={{ width: '100%' }}>
                  <Alert type="info" showIcon message={`${editingTaskId ? '保存后' : '创建后'}由后端持久化与调度执行，产物继续写入 outputs/。`} />
                  <Typography.Paragraph>
                    当前预览文件：{selectedFile?.name || '-'}，有效因子 {preview?.items.length || 0} 个。
                  </Typography.Paragraph>
                  <Typography.Paragraph>
                    任务将按 {watchedScheduleType ? SCHEDULE_TYPE_LABELS[watchedScheduleType] : '-'} 周期执行，
                    模式为 {TASK_MODE_LABELS.phase1_phase2}。
                  </Typography.Paragraph>
                </Space>
              </Card>
            ) : null}
          </Form>
        </Space>
      </Drawer>

      <Drawer
        className="drawer-compact"
        title={detailTask?.taskName || '任务详情'}
        width="min(960px, 90vw)"
        open={pageActive && detailOpen}
        mask={false}
        onClose={() => setDetailOpen(false)}
      >
        {detailTask ? (
          <ScheduledTaskDetail
            task={detailTask}
            onNotice={onNotice}
            onRefreshTask={async () => {
              const nextTask = await fetchScheduledTask(detailTask.taskId)
              setDetailTask(nextTask)
              await loadTasks()
            }}
          />
        ) : null}
      </Drawer>
    </Space>
  )
}
