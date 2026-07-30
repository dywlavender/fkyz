export type ReviewConclusion = 'pass' | 'fail' | 'unreviewable' | string

export interface TaskErrorMetadata {
  errorCode?: string
  errorCategory?: string
  suggestedAction?: string
}

export type ReviewVerdict =
  | 'pass'
  | 'fail'
  | 'unreviewable'
  | string

export interface ApiDocItem {
  name: string
  path: string
  category?: string
  provider?: string
  version?: string
  status?: string
  method?: string
  url?: string
  scenes?: string[]
  keywords?: string[]
  content?: string
  content_sha256?: string
  content_size?: number
}

export interface FactorProcessingSpecView {
  spec: {
    schema_version?: string
    spec_version?: string
    title?: string
    rules?: Array<Record<string, unknown>>
  }
  markdown: string
}

export interface FactorProcessingSpecResponse {
  phase1: FactorProcessingSpecView
  phase2: FactorProcessingSpecView
}

export interface TaskLog {
  time?: number
  message?: string
  stream?: string
}

export interface TaskRecord<T = unknown> {
  id?: string
  taskId?: string
  recordId?: string
  status?: string
  result?: T
  caseIds?: string[]
  outputs?: OutputArtifact[]
  logs?: TaskLog[]
  debugLogs?: TaskLog[]
  created?: number
  updated?: number
  taskType?: string
  clientId?: string
  parentTaskId?: string
  persisted?: boolean
  source?: string
  conclusion?: string
  riskCount?: number
  progress?: {
    total?: number
    completed?: number
    failed?: number
  }
  poolStatus?: string | null
  queuePosition?: number | null
  poolQueued?: number
  poolActive?: number
  context?: {
    taskName?: string
    factorName?: string
    requirement?: string
    standardFileId?: string
    apiNames?: string[]
  }
}

export interface RequirementReviewRequest {
  requirement: string
  apiNames: string[]
  clientId?: string
}

export interface RequirementReviewResponse extends TaskErrorMetadata {
  returncode?: number
  stdout?: string
  stderr?: string
  reviewFileId?: string
  reviewMetadataFileId?: string
  conclusion?: 'pass' | 'fail' | string
  success?: boolean
}

export interface GlobalPoolStatus {
  maxWorkers: number
  activeWorkers: number
  idleWorkers: number
  queuedJobs: number
  activeGroups: number
  queue?: Array<{
    jobId: string
    groupId: string
    position: number
    queuedAt: number
  }>
}

export interface OutputArtifact {
  name: string
  path: string
  mtime: number
  size: number
}

export interface Phase1Request {
  factorName: string
  requirement: string
  apiNames: string[]
  debugClaude?: boolean
  clientId?: string
  forceRefresh?: boolean
}

export interface Phase1Response {
  returncode?: number
  stdout?: string
  stderr?: string
  checksum?: string
  cacheHit?: boolean
  standardFileId?: string
  standardMetadataFileId?: string
  standardRawFileId?: string
  success?: boolean
}

export interface StandardMetadataV5 {
  schema_version: 'risk_review_standard_metadata_v5'
  standard_schema_version: 'risk_review_standard_v5'
  standard_file: string
  display_metadata?: {
    factor_name?: string
    factor_slug?: string
  }
  source_inputs?: {
    requirement?: string
    api_docs?: string[]
  }
  phase1_spec?: {
    schema_version?: string
    spec_version?: string
    checksum?: string
  }
  logic_types?: string[]
}

export interface Phase2Request {
  factorName: string
  requirement: string
  draft: string
  standardFileId: string
  debugClaude?: boolean
  clientId?: string
}

export interface Phase2Response {
  returncode?: number
  stdout?: string
  stderr?: string
  reviewFileId?: string
  reviewRawFileId?: string
  reviewChecksum?: string
  success?: boolean
}

export interface ReviewResult {
  conclusion?: ReviewConclusion
  riskLevel?: string
  summary?: string
  rawMarkdown?: string
  rawText?: string
}

export interface HistoryItem {
  id: string
  fileName: string
  type: 'phase1' | 'phase2' | 'other'
  factorName: string
  conclusion: ReviewConclusion
  createdAt: number
  size: number
  markdownFile?: string
  jsonFile?: string
  rawFile?: string
  standardFileId?: string
}

export interface ArtifactGroup {
  md?: string
  json?: string
  raw?: string
  validation?: string
}

export interface ReviewHistoryRecord {
  recordId: string
  id?: string
  factorName?: string
  createdAt?: string
  source?: 'single' | 'batch' | 'scheduled' | 'validation' | string
  mode?: 'phase1_only' | 'phase2_only' | 'one_click' | string
  conclusion?: string
  riskCount?: number
  title?: string
  taskId?: string
  taskType?: string
  status?: string
  clientId?: string
  parentTaskId?: string
  context?: TaskRecord['context']
  progress?: TaskRecord['progress']
  taskStatus?: string
  updatedAt?: string
  archived?: boolean
  summary?: {
    total?: number
    completed?: number
    failed?: number
  }
  items?: BatchResultItem[]
  standardArtifacts?: ArtifactGroup
  reviewArtifacts?: ArtifactGroup
}

export interface ReviewRecordPage {
  items: ReviewHistoryRecord[]
  page: number
  pageSize: number
  total: number
}

export interface ReviewRecordQuery {
  page?: number
  pageSize?: number
  keyword?: string
  source?: string
  taskStatus?: string
  conclusion?: string
  archived?: boolean
  dateFrom?: string
  dateTo?: string
  export?: boolean
}

export interface ExampleItem {
  id: string
  title?: string
  kind?: string
  factorName?: string
  requirement?: string
  apiNames?: string[]
  paramFields?: string
  procLogic?: string
  reviewContent?: string
  defaultValues?: string
  draft?: string
  expected?: string
  expectedIssuePatterns?: string[]
  forbiddenIssuePatterns?: string[]
  maxIssueCount?: number | null
  version?: string
  updatedAt?: number
}

export type ExpectedConclusion = 'pass' | 'fail' | 'unreviewable' | string

export interface ValidationCase {
  caseId: string
  caseName: string
  factorName: string
  requirement: string
  reviewContent?: string
  expectedConclusion?: ExpectedConclusion
  expectedIssuePatterns?: string[]
  forbiddenIssuePatterns?: string[]
  maxIssueCount?: number | null
  apiDocIds?: string[]
  businessRuleIds?: string[]
  tags?: string[]
  remark?: string
  createdAt?: string
  updatedAt?: string
  lastRunAt?: string
  lastActualConclusion?: string
  lastMatched?: boolean
  lastRunId?: string
  lastRunStatus?: string
}

export interface CreateValidationCaseRequest {
  caseName: string
  factorName: string
  requirement: string
  reviewContent: string
  expectedConclusion?: ExpectedConclusion
  expectedIssuePatterns?: string[]
  forbiddenIssuePatterns?: string[]
  maxIssueCount?: number | null
  apiDocIds?: string[]
  businessRuleIds?: string[]
  tags?: string[]
  remark?: string
}

export interface UpdateValidationCaseRequest extends Partial<CreateValidationCaseRequest> {}

export interface ValidationCaseImportError {
  rowNumber: number
  caseId?: string
  caseName?: string
  factorName?: string
  error: string
}

export interface ValidationCaseImportResult {
  fileName: string
  totalRows: number
  success: number
  failed: number
  items: ValidationCase[]
  errors: ValidationCaseImportError[]
  columns: string[]
}

export interface ValidationRun extends TaskErrorMetadata {
  groupId?: string
  runId: string
  caseId: string
  caseName?: string
  status: 'pending' | 'running' | 'success' | 'failed' | 'partial_success' | string
  expectedConclusion?: string
  expectedIssuePatterns?: string[]
  forbiddenIssuePatterns?: string[]
  maxIssueCount?: number | null
  actualConclusion?: string
  conclusionMatched?: boolean
  issuesMatched?: boolean
  issueEvaluation?: {
    configured?: boolean
    matched?: boolean
    missingIssuePatterns?: string[]
    forbiddenIssueHits?: string[]
    actualIssueCount?: number
    maxIssueCount?: number | null
  }
  matched?: boolean
  standardFile?: string
  standardMetadataFile?: string
  reviewFile?: string
  startedAt?: string
  finishedAt?: string
  error?: string
}

export interface BatchOperationResult {
  total: number
  success: number
  failed: number
  items: Array<{
    id: string
    success: boolean
    error?: string
  }>
}

export interface BatchValidationRun {
  groupId?: string
  total: number
  success: number
  failed: number
  unreviewable: number
  mismatched: number
  items: Array<{
    id: string
    success: boolean
    error?: string
    run?: ValidationRun
  }>
}

export interface ReviewModeResult {
  status: 'pending' | 'running' | 'success' | 'error' | 'cancelled' | string
  actualConclusion?: string
  conclusionMatched?: boolean
  issuesMatched?: boolean | null
  matched?: boolean
  issueEvaluation?: ValidationRun['issueEvaluation']
  issueStats?: {
    fail?: number
    pending?: number
    suggest?: number
  }
  durationMs?: number
  standardFile?: string
  standardMetadataFile?: string
  reviewFile?: string
  error?: string
}

export interface ReviewModeExperimentItem {
  itemId: string
  caseId: string
  caseName: string
  round: number
  expectedConclusion?: string
  issueBaselineConfigured?: boolean
  direct: ReviewModeResult
  twoStage: ReviewModeResult
}

export interface ReviewModeMetric {
  completed: number
  success: number
  errors: number
  conclusionMatched: number
  issueEvaluated: number
  issuesMatched: number
  fullyMatched: number
  conclusionAccuracy?: number | null
  issueAccuracy?: number | null
  fullAccuracy?: number | null
  averageDurationMs?: number | null
}

export interface ReviewModeExperiment {
  experimentId: string
  taskId: string
  status: 'queued' | 'running' | 'cancelling' | 'cancelled' | 'done' | 'error' | string
  caseIds: string[]
  caseCount: number
  repetitions: number
  createdAt: string
  startedAt?: string | null
  finishedAt?: string | null
  items: ReviewModeExperimentItem[]
  summary: {
    total: number
    completed: number
    direct: ReviewModeMetric
    twoStage: ReviewModeMetric
    winner: 'direct' | 'twoStage' | 'tie' | string
  }
  error?: string | null
}

export interface RegressionTaskItem extends TaskErrorMetadata {
  index?: number
  id?: string
  title?: string
  factorName?: string
  status?: string
  verdict?: string
  expectedOk?: boolean
  conclusionMatched?: boolean
  issuesMatched?: boolean
  issueEvaluation?: ValidationRun['issueEvaluation']
  expected?: string
  raw?: string
  standardText?: string
  standardFileId?: string | null
  standardMetadataFileId?: string | null
  reviewFileId?: string | null
  cacheHit?: boolean
  reportPreview?: string
  time?: number
  summary?: {
    fail?: number
    pending?: number
    suggest?: number
  }
}

export interface RegressionTaskResult {
  items?: RegressionTaskItem[]
  summary?: {
    total?: number
    pass?: number
    fail?: number
    error?: number
    skip?: number
    cacheHit?: number
    running?: number
    currentIndex?: number | null
    currentTitle?: string
  }
  stdout?: string
  stderr?: string
  returncode?: number
}

export interface BatchRow {
  caseName?: string
  factorName?: string
  requirement?: string
  apiNames?: string[]
  apiDocs?: string
  paramFields?: string
  procLogic?: string
  defaultValues?: string
  defaultVal?: string
  draft?: string
  remark?: string
}

export interface BatchResultItem extends TaskErrorMetadata {
  index?: number
  factorName?: string
  requirement?: string
  apiNames?: string[]
  paramFields?: string
  procLogic?: string
  defaultValues?: string
  draft?: string
  status?: string
  raw?: string
  standardText?: string
  standardFileId?: string | null
  standardMetadataFileId?: string | null
  reviewFileId?: string | null
  cacheHit?: boolean
  skipReason?: string
}

export interface RegressionSummary {
  total?: number
  passed?: number
  failed?: number
  untested?: number
  matched?: number
}

export interface SystemHealth {
  ok?: boolean
  checks?: Record<string, boolean>
  claude?: {
    command?: string
    claude_path?: string | null
    version?: string | null
    working_dir?: string
    mock_mode?: boolean
    note?: string
  }
  modelRuntime?: {
    selectedBackend?: 'claude' | 'agent'
    selectedAvailable?: boolean
    claude?: {
      available?: boolean
      command?: string
      claude_path?: string | null
      version?: string | null
      note?: string
    }
    agent?: {
      available?: boolean
      python?: string
      environment_available?: boolean
      provider?: string
      model?: string
      api_key_configured?: boolean
      langchain_version?: string | null
      note?: string
    }
  }
  outputs?: {
    path?: string
    exists?: boolean
    writable?: boolean
    note?: string
  }
  api_docs?: {
    count?: number
    by_category?: Record<string, number>
    note?: string
  }
  files?: Record<string, { path?: string; exists?: boolean; size?: number }>
  runtime_settings?: RuntimeSettings
}

export interface RuntimeSettings {
  globalConcurrency: number
  batchConcurrency: number
  scheduledConcurrency: number
  modelBackend: 'claude' | 'agent'
}

export interface AppNotice {
  type: 'success' | 'info' | 'warning' | 'error'
  message: string
  description?: string
}

export type ScheduleType = 'once' | 'daily' | 'weekly' | 'monthly' | 'cron'
export type ScheduledTaskMode = 'phase1_only' | 'phase1_phase2' | 'phase2_only'
export type ScheduledTaskStatus = 'enabled' | 'disabled'
export type ScheduledRunStatus = 'pending' | 'running' | 'success' | 'partial_success' | 'failed' | 'cancelled'

export interface ScheduledTaskItem {
  factorName: string
  requirement: string
  paramFields?: string
  procLogic?: string
  reviewContent?: string
  defaultVal?: string
  defaultValues?: string
  apiDocIds?: string[]
  businessRuleIds?: string[]
  standardFile?: string
  remark?: string
}

export interface ScheduledTask {
  taskId: string
  taskName: string
  enabled: boolean
  scheduleType: ScheduleType
  cron?: string
  runDate?: string
  runTime?: string
  weekday?: string
  monthDay?: string
  mode: ScheduledTaskMode
  runtimeMode: 'mock' | 'claude'
  failurePolicy: 'continue' | 'stop'
  standardFile?: string
  items: ScheduledTaskItem[]
  createdAt: string
  updatedAt: string
  lastRunAt?: string
  nextRunAt?: string
  itemCount?: number
  lastRunStatus?: ScheduledRunStatus | string | null
}

export interface ScheduledRunItem extends TaskErrorMetadata {
  index: number
  factorName: string
  status: string
  standardFile?: string
  reviewFile?: string
  reviewConclusion?: 'pass' | 'fail' | 'unreviewable' | string
  error?: string
}

export interface ScheduledRun {
  runId: string
  taskId: string
  taskName: string
  status: ScheduledRunStatus
  startedAt?: string
  finishedAt?: string
  total: number
  success: number
  failed: number
  unknown: number
  durationMs?: number
  items: ScheduledRunItem[]
}

export interface ImportPreviewError {
  rowNumber: number
  factorName?: string
  error: string
}

export interface ImportPreviewResult {
  fileName: string
  totalRows: number
  validRows: number
  errorRows: number
  items: ScheduledTaskItem[]
  errors: ImportPreviewError[]
  columns: string[]
}

export interface CreateScheduledTaskRequest {
  taskName: string
  enabled: boolean
  scheduleType: ScheduleType
  cron?: string
  runDate?: string
  runTime?: string
  weekday?: string
  monthDay?: string
  mode: ScheduledTaskMode
  runtimeMode: 'mock' | 'claude'
  failurePolicy: 'continue' | 'stop'
  standardFile?: string
  items: ScheduledTaskItem[]
}

export interface UpdateScheduledTaskRequest extends CreateScheduledTaskRequest {}
