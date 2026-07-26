import type {
  ApiDocItem,
  BatchOperationResult,
  BatchValidationRun,
  BatchRow,
  CreateValidationCaseRequest,
  CreateScheduledTaskRequest,
  ExampleItem,
  FactorProcessingSpecResponse,
  ImportPreviewResult,
  OutputArtifact,
  Phase1Request,
  Phase1Response,
  Phase2Request,
  Phase2Response,
  ReviewHistoryRecord,
  ReviewRecordPage,
  ReviewRecordQuery,
  ReviewModeExperiment,
  RuntimeSettings,
  ScheduledRun,
  ScheduledTask,
  SystemHealth,
  TaskRecord,
  UpdateScheduledTaskRequest,
  UpdateValidationCaseRequest,
  ValidationCase,
  ValidationCaseImportResult,
  ValidationRun,
  GlobalPoolStatus,
} from '../types/review'
import { groupOutputsToReviewHistory } from '../utils/reviewTransform'

export class AppError extends Error {
  status?: number
  details?: unknown

  constructor(message: string, status?: number, details?: unknown) {
    super(message)
    this.name = 'AppError'
    this.status = status
    this.details = details
  }
}

async function requestJson<T>(url: string, init?: RequestInit): Promise<T> {
  const response = await fetch(url, init)
  const contentType = response.headers.get('content-type') || ''
  const isJson = contentType.includes('application/json')
  const payload = isJson ? await response.json().catch(() => ({})) : await response.text().catch(() => '')

  if (!response.ok) {
    const message = typeof payload === 'string'
      ? payload || `请求失败：${response.status}`
      : payload?.error || payload?.message || `请求失败：${response.status}`
    throw new AppError(message, response.status, payload)
  }

  return payload as T
}

const CLIENT_ID_KEY = 'risk-review-client-id'

export function getClientId(): string {
  const existing = window.localStorage.getItem(CLIENT_ID_KEY)
  if (existing) return existing
  const value = typeof crypto?.randomUUID === 'function'
    ? crypto.randomUUID()
    : `browser-${Date.now()}-${Math.random().toString(36).slice(2)}`
  window.localStorage.setItem(CLIENT_ID_KEY, value)
  return value
}

async function requestApiData<T>(url: string, init?: RequestInit): Promise<T> {
  const response = await requestJson<{ success?: boolean; data?: T; error?: string; detail?: unknown }>(url, init)
  if (response?.success === false) {
    throw new AppError(response.error || '请求失败', undefined, response.detail)
  }
  return response.data as T
}

async function encodeFileBase64(file: File): Promise<string> {
  const buffer = await file.arrayBuffer()
  const binary = new Uint8Array(buffer)
  let raw = ''
  binary.forEach((item) => {
    raw += String.fromCharCode(item)
  })
  return btoa(raw)
}

export async function runPhase1(payload: Phase1Request): Promise<{ taskId: string; task: TaskRecord<Phase1Response> }> {
  return requestJson('/api/review/phase1', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ ...payload, clientId: payload.clientId || getClientId() }),
  })
}

export async function runPhase2(payload: Phase2Request): Promise<{ taskId: string; task: TaskRecord<Phase2Response> }> {
  return requestJson('/api/review/phase2', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ ...payload, clientId: payload.clientId || getClientId() }),
  })
}

export async function fetchTask(taskId: string): Promise<TaskRecord> {
  const data = await requestJson<{ task: TaskRecord }>(`/api/tasks/${encodeURIComponent(taskId)}`)
  return data.task
}

export async function cancelTask(taskId: string): Promise<TaskRecord> {
  const data = await requestJson<{ task: TaskRecord }>(`/api/tasks/${encodeURIComponent(taskId)}/cancel`, {
    method: 'POST',
  })
  return data.task
}

export async function pollTask<T = unknown>(
  taskId: string,
  options?: { interval?: number; timeoutMs?: number }
): Promise<TaskRecord<T>> {
  const interval = options?.interval ?? 1200
  const timeoutMs = options?.timeoutMs ?? 10 * 60 * 1000
  const start = Date.now()

  while (Date.now() - start < timeoutMs) {
    const task = await fetchTask(taskId) as TaskRecord<T>
    if (['done', 'error', 'cancelled'].includes(String(task.status || '').toLowerCase())) {
      return task
    }
    await new Promise((resolve) => setTimeout(resolve, interval))
  }

  throw new AppError('任务轮询超时，请稍后在历史记录中查看结果。')
}

export async function fetchHistory(): Promise<OutputArtifact[]> {
  const data = await requestJson<{ outputs: OutputArtifact[] }>('/api/outputs')
  return data.outputs || []
}

export async function fetchReviewHistory(): Promise<ReviewHistoryRecord[]> {
  try {
    return await requestApiData<ReviewHistoryRecord[]>('/api/review-history')
  } catch (error) {
    if (error instanceof AppError && error.status && ![404, 410].includes(error.status)) {
      throw error
    }
    const outputs = await fetchHistory()
    return groupOutputsToReviewHistory(outputs)
  }
}

export async function fetchReviewRecords(query: ReviewRecordQuery = {}): Promise<ReviewRecordPage> {
  const params = new URLSearchParams()
  Object.entries(query).forEach(([key, value]) => {
    if (value !== undefined && value !== null && value !== '') params.set(key, String(value))
  })
  return requestApiData<ReviewRecordPage>(`/api/review-records?${params.toString()}`)
}

export async function setReviewRecordArchived(recordId: string, archived: boolean): Promise<ReviewHistoryRecord> {
  return requestApiData<ReviewHistoryRecord>(`/api/review-records/${encodeURIComponent(recordId)}/archive`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ archived }),
  })
}

export async function fetchApiDocs(): Promise<ApiDocItem[]> {
  const data = await requestJson<{ apis: ApiDocItem[] }>('/api/apis')
  return data.apis || []
}

export async function fetchApiDocDetail(identifier: string): Promise<ApiDocItem> {
  const data = await requestJson<{ api: ApiDocItem }>(`/api/api-doc?name=${encodeURIComponent(identifier)}`)
  return data.api
}

export async function fetchExamples(): Promise<ExampleItem[]> {
  const data = await requestJson<{ examples: ExampleItem[] }>('/api/examples')
  return data.examples || []
}

export async function fetchBusinessRules(): Promise<Array<Record<string, unknown>>> {
  const data = await requestJson<{ rules: Array<Record<string, unknown>> }>('/api/business-rules')
  return data.rules || []
}

export async function fetchFactorProcessingSpec(): Promise<FactorProcessingSpecResponse> {
  return requestApiData<FactorProcessingSpecResponse>('/api/factor-processing-spec')
}

export async function fetchHealth(): Promise<SystemHealth> {
  return requestJson<SystemHealth>('/api/self-check')
}

export async function fetchTasks(): Promise<TaskRecord[]> {
  const data = await requestJson<{ tasks: TaskRecord[] }>('/api/tasks')
  return data.tasks || []
}

export async function fetchGlobalPoolStatus(): Promise<GlobalPoolStatus> {
  const data = await requestJson<{ pool: GlobalPoolStatus }>('/api/pool/status')
  return data.pool
}

export async function fetchRuntimeSettings(): Promise<RuntimeSettings> {
  return requestApiData<RuntimeSettings>('/api/runtime-settings')
}

export async function updateRuntimeSettings(payload: RuntimeSettings): Promise<RuntimeSettings> {
  return requestApiData<RuntimeSettings>('/api/runtime-settings', {
    method: 'PUT',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(payload),
  })
}

export async function fetchOutputText(fileId: string): Promise<string> {
  const response = await fetch(`/outputs/${encodeURIComponent(fileId)}`)
  if (!response.ok) {
    throw new AppError(`读取产物失败：${fileId}`, response.status)
  }
  return response.text()
}

export async function fetchArtifactContent(filename: string): Promise<string> {
  return fetchOutputText(filename)
}

export async function fetchOutputJson<T>(fileId: string): Promise<T> {
  const text = await fetchOutputText(fileId)
  try {
    return JSON.parse(text) as T
  } catch (error) {
    throw new AppError(`解析 JSON 失败：${fileId}`, undefined, error)
  }
}

export async function parseExcel(name: string, dataBase64: string): Promise<{ rows: string[][]; items: BatchRow[]; name: string }> {
  return requestJson('/api/parse-excel', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ name, data: dataBase64 }),
  })
}

export async function runBatchReview(payload: {
  rows: BatchRow[]
  mock?: boolean
  retry?: number
  resume?: boolean
}) {
  return requestJson<{ taskId: string; task: TaskRecord }>('/api/review/batch', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ ...payload, clientId: getClientId() }),
  })
}

export async function runRegression(payload: {
  exampleIds?: string[]
}) {
  return requestJson<{ taskId: string; task: TaskRecord }>('/api/validation/regression', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ ...payload, clientId: getClientId() }),
  })
}

export async function fetchValidationCases(): Promise<ValidationCase[]> {
  return requestApiData<ValidationCase[]>('/api/validation-cases')
}

export async function fetchValidationCase(caseId: string): Promise<ValidationCase> {
  return requestApiData<ValidationCase>(`/api/validation-cases/${encodeURIComponent(caseId)}`)
}

export async function createValidationCase(payload: CreateValidationCaseRequest): Promise<ValidationCase> {
  return requestApiData<ValidationCase>('/api/validation-cases', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(payload),
  })
}

export async function importValidationCases(file: File): Promise<ValidationCaseImportResult> {
  return requestApiData<ValidationCaseImportResult>('/api/validation-cases/import', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ name: file.name, data: await encodeFileBase64(file) }),
  })
}

export async function updateValidationCase(caseId: string, payload: UpdateValidationCaseRequest): Promise<ValidationCase> {
  return requestApiData<ValidationCase>(`/api/validation-cases/${encodeURIComponent(caseId)}`, {
    method: 'PUT',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(payload),
  })
}

export async function deleteValidationCase(caseId: string): Promise<void> {
  await requestApiData(`/api/validation-cases/${encodeURIComponent(caseId)}`, {
    method: 'DELETE',
  })
}

export async function batchDeleteValidationCases(caseIds: string[]): Promise<BatchOperationResult> {
  return requestApiData<BatchOperationResult>('/api/validation-cases/batch-delete', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ caseIds }),
  })
}

export async function runValidationCase(caseId: string): Promise<ValidationRun> {
  return requestApiData<ValidationRun>(`/api/validation-cases/${encodeURIComponent(caseId)}/run`, {
    method: 'POST',
  })
}

export async function batchRunValidationCases(caseIds: string[]): Promise<BatchValidationRun> {
  return requestApiData<BatchValidationRun>('/api/validation-cases/batch-run', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ caseIds }),
  })
}

export async function fetchValidationRuns(caseId?: string): Promise<ValidationRun[]> {
  const suffix = caseId ? `?caseId=${encodeURIComponent(caseId)}` : ''
  return requestApiData<ValidationRun[]>(`/api/validation-runs${suffix}`)
}

export async function fetchValidationRunGroups(page = 1, pageSize = 20): Promise<{
  items: Array<{
    groupId: string
    runs: ValidationRun[]
    startedAt?: string
    finishedAt?: string
    total: number
    matched: number
    mismatched: number
    failed: number
    status: string
    agreementRate: number
  }>
  page: number
  pageSize: number
  total: number
}> {
  return requestApiData(`/api/validation-run-groups?page=${page}&pageSize=${pageSize}`)
}

export async function batchDeleteValidationRunGroups(groupIds: string[]): Promise<BatchOperationResult> {
  return requestApiData<BatchOperationResult>('/api/validation-run-groups/batch-delete', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ groupIds }),
  })
}

export async function fetchValidationRun(runId: string): Promise<ValidationRun> {
  return requestApiData<ValidationRun>(`/api/validation-runs/${encodeURIComponent(runId)}`)
}

export async function createReviewModeExperiment(payload: {
  caseIds: string[]
  repetitions?: number
  mock?: boolean
}): Promise<{ experimentId: string; taskId: string; task: TaskRecord<ReviewModeExperiment> }> {
  return requestApiData<{ experimentId: string; taskId: string; task: TaskRecord<ReviewModeExperiment> }>('/api/review-mode-experiments', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ ...payload, clientId: getClientId() }),
  })
}

export async function fetchReviewModeExperiments(): Promise<ReviewModeExperiment[]> {
  return requestApiData<ReviewModeExperiment[]>('/api/review-mode-experiments')
}

export async function fetchReviewModeExperiment(experimentId: string): Promise<ReviewModeExperiment> {
  return requestApiData<ReviewModeExperiment>(`/api/review-mode-experiments/${encodeURIComponent(experimentId)}`)
}

export async function deleteHistoryItem(_id: string): Promise<void> {
  return deleteReviewHistoryRecord(_id)
}

export async function deleteReviewHistoryRecord(recordId: string): Promise<void> {
  await requestApiData(`/api/review-history/${encodeURIComponent(recordId)}`, {
    method: 'DELETE',
  })
}

export async function previewScheduledTaskImport(
  file: File,
  options?: { mode?: string; standardFile?: string }
): Promise<ImportPreviewResult> {
  const data = await encodeFileBase64(file)
  return requestApiData<ImportPreviewResult>('/api/scheduled-tasks/import-preview', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({
      name: file.name,
      data,
      mode: options?.mode,
      standardFile: options?.standardFile,
    }),
  })
}

export async function createScheduledTask(payload: CreateScheduledTaskRequest): Promise<ScheduledTask> {
  return requestApiData<ScheduledTask>('/api/scheduled-tasks', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(payload),
  })
}

export async function fetchScheduledTasks(): Promise<ScheduledTask[]> {
  return requestApiData<ScheduledTask[]>('/api/scheduled-tasks')
}

export async function fetchScheduledTask(taskId: string): Promise<ScheduledTask> {
  return requestApiData<ScheduledTask>(`/api/scheduled-tasks/${encodeURIComponent(taskId)}`)
}

export async function updateScheduledTask(taskId: string, payload: UpdateScheduledTaskRequest): Promise<ScheduledTask> {
  return requestApiData<ScheduledTask>(`/api/scheduled-tasks/${encodeURIComponent(taskId)}`, {
    method: 'PUT',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(payload),
  })
}

export async function deleteScheduledTask(taskId: string): Promise<void> {
  await requestApiData(`/api/scheduled-tasks/${encodeURIComponent(taskId)}`, {
    method: 'DELETE',
  })
}

export async function enableScheduledTask(taskId: string): Promise<ScheduledTask> {
  return requestApiData<ScheduledTask>(`/api/scheduled-tasks/${encodeURIComponent(taskId)}/enable`, {
    method: 'POST',
  })
}

export async function disableScheduledTask(taskId: string): Promise<ScheduledTask> {
  return requestApiData<ScheduledTask>(`/api/scheduled-tasks/${encodeURIComponent(taskId)}/disable`, {
    method: 'POST',
  })
}

export async function runScheduledTaskNow(taskId: string): Promise<ScheduledRun> {
  return requestApiData<ScheduledRun>(`/api/scheduled-tasks/${encodeURIComponent(taskId)}/run-now`, {
    method: 'POST',
  })
}

export async function fetchScheduledRuns(taskId: string): Promise<ScheduledRun[]> {
  return requestApiData<ScheduledRun[]>(`/api/scheduled-tasks/${encodeURIComponent(taskId)}/runs`)
}

export async function fetchScheduledRun(runId: string): Promise<ScheduledRun> {
  return requestApiData<ScheduledRun>(`/api/scheduled-runs/${encodeURIComponent(runId)}`)
}

export async function cancelScheduledRun(runId: string): Promise<ScheduledRun> {
  return requestApiData<ScheduledRun>(`/api/scheduled-runs/${encodeURIComponent(runId)}/cancel`, {
    method: 'POST',
  })
}
