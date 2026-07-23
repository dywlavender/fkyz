export type ReviewConclusion = 'pass' | 'fail' | 'confirm' | 'suggestion' | string

export type ReviewVerdict =
  | 'pass'
  | 'fail'
  | 'confirm'
  | 'suggestion'
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

export interface TaskLog {
  time?: number
  message?: string
  stream?: string
}

export interface TaskRecord<T = unknown> {
  id?: string
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
  poolStatus?: string | null
  queuePosition?: number | null
  poolQueued?: number
  poolActive?: number
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
  skipReviewer?: boolean
  skipStageValidation?: boolean
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
  standardJsonFileId?: string
  standardSummaryFileId?: string
  standardRawFileId?: string
  standardValidationFileId?: string
  standardJsonChecksum?: string
  reviewer?: unknown
  reviewerSkipped?: boolean
  success?: boolean
  blocking?: boolean
  validationSummary?: ValidationSummary
  validationItems?: ValidationItem[]
}

export interface Phase2Request {
  factorName: string
  requirement: string
  apiNames: string[]
  draft: string
  standardFileId: string
  debugClaude?: boolean
  skipReviewer?: boolean
  skipStageValidation?: boolean
  clientId?: string
}

export interface Phase2Response {
  returncode?: number
  stdout?: string
  stderr?: string
  reviewFileId?: string
  reviewJsonFileId?: string
  reviewRawFileId?: string
  reviewValidationFileId?: string
  reviewChecksum?: string
  reviewer?: unknown
  reviewerSkipped?: boolean
  success?: boolean
  blocking?: boolean
  degraded?: boolean
  validationSummary?: ValidationSummary
  validationItems?: ValidationItem[]
}

export interface ValidationSummary {
  blocking?: boolean
  errorCount?: number
  warningCount?: number
  autoFixCount?: number
  totalCount?: number
}

export interface ValidationItem {
  field?: string
  message?: string
  severity?: 'error' | 'warning' | 'autoFix' | string
  blocking?: boolean
  autoFixed?: boolean
  before?: unknown
  after?: unknown
}

export interface RuleCheck {
  id?: string
  name?: string
  rule?: string
  conclusion?: ReviewConclusion
  evidence?: string
  risk?: string
  suggestion?: string
  ruleId?: string
  verdict?: ReviewVerdict
  standard?: string
  standardEvidence?: string[]
  actualEvidence?: string[]
  reason?: string
}

export interface StepCheck {
  id?: string
  name?: string
  step?: string
  conclusion?: ReviewConclusion
  evidence?: string
  risk?: string
  suggestion?: string
  stepNo?: number
  stepType?: string
  verdict?: ReviewVerdict
  standardStep?: string
  actualEvidence?: string[]
  reason?: string
  relatedRuleIds?: string[]
}

export interface ReviewIssue {
  id?: string
  dimension?: string
  level?: string
  ruleId?: string
  relatedRuleIds?: string[]
  standard?: string
  actual?: string
  message?: string
  impact?: string
  suggestion?: string
  stepNo?: number
  stepType?: string
}

export interface ReviewResult {
  conclusion?: ReviewConclusion
  riskLevel?: string
  summary?: string
  ruleChecks?: RuleCheck[]
  stepChecks?: StepCheck[]
  rawMarkdown?: string
  rawText?: string
  issues?: ReviewIssue[]
  checks?: Record<string, string>
  majorRisks?: string[]
  suggestions?: string[]
}

export interface ReviewSummaryPayload {
  overall?: string
  majorRisks?: string[]
  suggestions?: string[]
}

export interface ReviewJsonPayload {
  schemaVersion?: string
  phase?: string
  factorName?: string
  conclusion?: ReviewVerdict
  conclusionBySystem?: ReviewVerdict
  finalConclusion?: ReviewVerdict
  summary?: ReviewSummaryPayload
  ruleChecks?: RuleCheck[]
  stepChecks?: StepCheck[]
  issues?: ReviewIssue[]
  checks?: Record<string, string>
}

export interface StandardSource {
  requirementSummary?: string
  apiDocs?: Array<{ name?: string; path?: string }>
  businessRuleIds?: string[]
}

export interface StandardStep {
  stepNo?: number
  type?: string
  description?: string
  inputFields?: string[]
  outputField?: string
  basisRuleIds?: string[]
}

export interface StandardRule {
  ruleId?: string
  dimension?: string
  basisType?: string
  content?: string
  evidence?: string[]
}

export interface Phase1Standard {
  schemaVersion?: string
  standardVersion?: string
  revisionReason?: string
  phase?: string
  factorName?: string
  source?: StandardSource
  summary?: {
    objective?: string
    processingOverview?: string
    riskNotes?: string[]
  }
  processingLogic?: {
    object?: string
    logicType?: string
    steps?: StandardStep[]
  }
  inputs?: Array<{
    fieldPath?: string
    fieldName?: string
    sourceApi?: string
    role?: string
    evidence?: string[]
  }>
  coverageChecks?: Record<string, string>
  arrayProcessingSpec?: Array<Record<string, unknown>>
  outputContract?: Record<string, unknown>
  rules?: StandardRule[]
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
  factorName?: string
  createdAt?: string
  source?: 'single' | 'batch' | 'scheduled' | 'validation' | string
  mode?: 'phase1_only' | 'phase2_only' | 'one_click' | string
  conclusion?: string
  riskCount?: number
  standardArtifacts?: ArtifactGroup
  reviewArtifacts?: ArtifactGroup
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

export type ExpectedConclusion = 'pass' | 'fail' | 'confirm' | 'suggestion' | string

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

export interface ValidationRun {
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
  standardJsonFile?: string
  reviewFile?: string
  reviewJsonFile?: string
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
  confirm: number
  mismatched: number
  items: Array<{
    id: string
    success: boolean
    error?: string
    run?: ValidationRun
  }>
}

export interface RegressionTaskItem {
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
  standardSummaryFileId?: string | null
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
  factorName?: string
  requirement?: string
  apiNames?: string[]
  apiDocs?: string
  paramFields?: string
  procLogic?: string
  defaultValues?: string
  defaultVal?: string
  draft?: string
}

export interface BatchResultItem {
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
  standardSummaryFileId?: string | null
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
  concurrency: number
  failurePolicy: 'continue' | 'stop'
  skipReviewer: boolean
  skipStageValidation: boolean
  standardFile?: string
  items: ScheduledTaskItem[]
  createdAt: string
  updatedAt: string
  lastRunAt?: string
  nextRunAt?: string
  itemCount?: number
  lastRunStatus?: ScheduledRunStatus | string | null
}

export interface ScheduledRunItem {
  index: number
  factorName: string
  status: string
  standardFile?: string
  reviewFile?: string
  reviewConclusion?: 'pass' | 'fail' | 'unknown' | string
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
  concurrency: number
  failurePolicy: 'continue' | 'stop'
  skipReviewer: boolean
  skipStageValidation: boolean
  standardFile?: string
  items: ScheduledTaskItem[]
}

export interface UpdateScheduledTaskRequest extends CreateScheduledTaskRequest {}
