import type {
  HistoryItem,
  OutputArtifact,
  Phase1Standard,
  ReviewHistoryRecord,
  ReviewJsonPayload,
  ReviewResult,
  ReviewVerdict,
} from '../types/review'

const CONCLUSION_LABELS: Record<string, string> = {
  pass: 'pass',
  fail: 'fail',
  confirm: 'confirm',
  suggestion: 'suggestion',
}

interface TaskFailureSource {
  stderr?: string
  stdout?: string
  errorCategory?: string
  suggestedAction?: string
}

export function taskFailureMessage(result: TaskFailureSource | null | undefined, fallback: string): string {
  const stderr = String(result?.stderr || '').trim()
  if (stderr) {
    const category = String(result?.errorCategory || '').trim()
    const action = String(result?.suggestedAction || '').trim()
    return [category ? `${category}：${stderr}` : stderr, action].filter(Boolean).join('；')
  }
  const explicitError = String(result?.stdout || '')
    .split(/\r?\n/)
    .map((line) => line.trim())
    .find((line) => /^(?:API Error|Error|错误)\s*[:：]/i.test(line))
  return explicitError || fallback
}

export function mapVerdictToConclusion(value?: string): string {
  if (!value) return 'confirm'
  const normalized = String(value).trim().toLowerCase()
  return CONCLUSION_LABELS[normalized] ?? normalized
}

export function verdictTone(value?: string): 'success' | 'error' | 'warning' | 'default' {
  const normalized = String(value || '').trim().toLowerCase()
  if (normalized === 'pass') return 'success'
  if (normalized === 'fail') return 'error'
  if (normalized === 'confirm') return 'warning'
  return 'default'
}

export function humanFileSize(size?: number): string {
  const target = size ?? 0
  if (target < 1024) return `${target} B`
  if (target < 1024 * 1024) return `${(target / 1024).toFixed(1)} KB`
  return `${(target / 1024 / 1024).toFixed(1)} MB`
}

export function formatDateTime(timestamp?: number): string {
  if (!timestamp) return '-'
  return new Date(timestamp * (timestamp > 1_000_000_000_000 ? 1 : 1000)).toLocaleString('zh-CN', {
    hour12: false,
  })
}

export function formatDateTimeValue(value?: number | string): string {
  if (!value) return '-'
  if (typeof value === 'number') return formatDateTime(value)
  const date = new Date(value)
  if (Number.isNaN(date.getTime())) return value
  return date.toLocaleString('zh-CN', { hour12: false })
}

export function buildDraftFromSections(paramFields: string, procLogic: string, defaultValues: string): string {
  const sections: string[] = []
  if (paramFields.trim()) sections.push(`【因子加工输入字段】\n${paramFields.trim()}`)
  if (procLogic.trim()) sections.push(`【加工逻辑】\n${procLogic.trim()}`)
  if (defaultValues.trim()) sections.push(`【默认值】\n${defaultValues.trim()}`)
  return sections.join('\n\n')
}

export function parseDraftSections(draft?: string): { paramFields: string; procLogic: string; defaultValues: string } {
  const source = String(draft || '').trim()
  if (!source) return { paramFields: '', procLogic: '', defaultValues: '' }
  const aliases: Record<string, 'paramFields' | 'procLogic' | 'defaultValues'> = {
    因子加工输入字段: 'paramFields',
    加工逻辑: 'procLogic',
    默认值: 'defaultValues',
  }
  const pattern = /(?:^|\n)\s*(?:【([^】]+)】|#+\s*([^\n#]+)|([^\n：:]{2,16})[：:])\s*\n?/g
  const matches = Array.from(source.matchAll(pattern))
    .map((match) => ({
      title: String(match[1] || match[2] || match[3] || '').trim(),
      index: match.index ?? 0,
      end: (match.index ?? 0) + match[0].length,
    }))
    .filter((match) => aliases[match.title])

  if (!matches.length) return { paramFields: '', procLogic: source, defaultValues: '' }

  const result = { paramFields: '', procLogic: '', defaultValues: '' }
  matches.forEach((match, index) => {
    const key = aliases[match.title]
    const nextIndex = matches[index + 1]?.index ?? source.length
    const value = source.slice(match.end, nextIndex).trim()
    if (value) result[key] = result[key] ? `${result[key]}\n\n${value}` : value
  })
  const prefix = source.slice(0, matches[0].index).trim()
  if (prefix) result.procLogic = result.procLogic ? `${prefix}\n\n${result.procLogic}` : prefix
  return result
}

export function parseReviewResult(review: ReviewJsonPayload | null, rawMarkdown?: string): ReviewResult {
  const summary = review?.summary
  const markdownConclusion = (() => {
    const overall = String(rawMarkdown || '').match(/\|\s*总体结论\s*\|\s*([^|]+)\|/)?.[1] || ''
    if (/✅|通过/.test(overall) && !/不通过/.test(overall)) return 'pass'
    if (/❌|不通过|不符合/.test(overall)) return 'fail'
    if (/⚠️|建议/.test(overall)) return 'suggestion'
    return 'confirm'
  })()
  const conclusion = review
    ? mapVerdictToConclusion(review.conclusionBySystem || review.conclusion || review.finalConclusion)
    : markdownConclusion
  return {
    conclusion,
    riskLevel: review?.conclusionBySystem || review?.conclusion || review?.finalConclusion || conclusion,
    summary: summary?.overall || (rawMarkdown ? '评审报告已生成，结构化明细请查看原始产物。' : ''),
    ruleChecks: review?.ruleChecks || [],
    stepChecks: review?.stepChecks || [],
    rawMarkdown,
    rawText: rawMarkdown,
    issues: review?.issues || [],
    checks: review?.checks || {},
    majorRisks: summary?.majorRisks || [],
    suggestions: summary?.suggestions || [],
  }
}

export function parseStandardFromJson(data: unknown): Phase1Standard | null {
  if (!data || typeof data !== 'object') return null
  const source = data as Record<string, unknown>
  if (source.schema_version !== 'risk_review_standard_json_v4') {
    return data as Phase1Standard
  }
  const rules = Array.isArray(source.rules) ? source.rules as Array<Record<string, unknown>> : []
  const calculationRules = rules.filter((rule) => String(rule.dimension_ref || '') === 'D5')
  return {
    schemaVersion: String(source.schema_version),
    standardVersion: 'v4',
    phase: 'phase1',
    factorName: String(source.factor_name || ''),
    summary: {
      objective: '生成唯一、最可能、可执行的因子加工标准',
      processingOverview: String(source.calculation || ''),
      riskNotes: [],
    },
    processingLogic: {
      object: '因子加工主链',
      logicType: 'deterministic',
      steps: calculationRules.map((rule, index) => ({
        stepNo: index + 1,
        type: String(rule.dimension_ref || 'D5'),
        description: String(rule.content || ''),
        inputFields: Array.isArray(rule.field_paths) ? rule.field_paths.map(String) : [],
        basisRuleIds: [String(rule.rule_id || '')].filter(Boolean),
      })),
    },
    rules: rules.map((rule) => ({
      ruleId: String(rule.rule_id || ''),
      dimension: String(rule.dimension_ref || rule.dimension || ''),
      basisType: String(rule.basis_type || ''),
      content: String(rule.content || ''),
      evidence: Array.isArray(rule.evidence_ids) ? rule.evidence_ids.map(String) : [],
    })),
    arrayProcessingSpec: Array.isArray(source.selected_interpretations)
      ? source.selected_interpretations as Array<Record<string, unknown>>
      : [],
  }
}

export function factorNameFromFileName(name: string): string {
  const currentMatch = name.match(/^(?:standard|review)-(.+?)-\d{4}-\d{2}-\d{2}-[a-z0-9]{8}-[a-z0-9]{8}(?:\.|$)/i)
  if (currentMatch) return currentMatch[1].replace(/-/g, ' ').trim()
  const match = name.match(/^(?:standard|review)-(.+)-\d{4}-\d{2}-\d{2}-[a-z0-9]{8}\./i)
  if (!match) return name
  return match[1].replace(/-/g, ' ').trim()
}

export function groupHistoryArtifacts(outputs: OutputArtifact[]): HistoryItem[] {
  const grouped = new Map<string, HistoryItem>()

  for (const output of outputs) {
    const name = output.name
    const type = name.includes('.review.') || name.endsWith('.review.md')
      ? 'phase2'
      : name.includes('.standard.') || name.endsWith('.standard.md')
        ? 'phase1'
        : 'other'
    const key = name
      .replace(/\.review\.(json|md|raw\.txt|validation\.json)$/i, '')
      .replace(/\.standard\.(json|md|raw\.txt|validation\.json)$/i, '')
      .replace(/\.summary\.json$/i, '')
    const existing = grouped.get(key) ?? {
      id: key,
      fileName: name,
      type,
      factorName: factorNameFromFileName(name),
      conclusion: 'confirm',
      createdAt: output.mtime,
      size: output.size,
    }

    existing.createdAt = Math.max(existing.createdAt, output.mtime)
    existing.size = Math.max(existing.size, output.size)
    if (name.endsWith('.md')) existing.markdownFile = name
    if (name.endsWith('.json') && !name.endsWith('.validation.json') && !name.endsWith('.summary.json')) {
      existing.jsonFile = name
    }
    if (name.endsWith('.raw.txt')) existing.rawFile = name
    if (type === 'phase1' && name.endsWith('.standard.md')) existing.standardFileId = name
    grouped.set(key, existing)
  }

  return Array.from(grouped.values()).sort((a, b) => b.createdAt - a.createdAt)
}

function recordIdFromBase(base: string): string {
  let hash = 0
  for (let index = 0; index < base.length; index += 1) {
    hash = (hash * 31 + base.charCodeAt(index)) >>> 0
  }
  return `session-${hash.toString(16).padStart(8, '0')}`
}

function parseArtifactName(name: string): { phase: 'standard' | 'review'; base: string; kind: keyof NonNullable<ReviewHistoryRecord['standardArtifacts']> } | null {
  const match = name.match(/^(standard|review)-(.+)\.(standard|review)\.(md|json|raw\.txt|validation\.json)$/i)
  if (match && match[1] === match[3]) {
    const ext = match[4].toLowerCase()
    const kind = ext === 'raw.txt' ? 'raw' : ext === 'validation.json' ? 'validation' : ext
    return {
      phase: match[1] as 'standard' | 'review',
      base: match[2],
      kind: kind as keyof NonNullable<ReviewHistoryRecord['standardArtifacts']>,
    }
  }
  const current = name.match(/^(standard|review)-(.+)\.(md|summary\.json|raw\.txt|validation\.json)$/i)
  if (!current) return null
  const ext = current[3].toLowerCase()
  const kind = ext === 'summary.json' ? 'json' : ext === 'raw.txt' ? 'raw' : ext === 'validation.json' ? 'validation' : 'md'
  return {
    phase: current[1] as 'standard' | 'review',
    base: `${current[1]}-${current[2]}`,
    kind,
  }
}

export function groupOutputsToReviewHistory(outputs: OutputArtifact[]): ReviewHistoryRecord[] {
  const grouped = new Map<string, ReviewHistoryRecord & { createdAtNumber?: number }>()

  for (const output of outputs) {
    const parsed = parseArtifactName(output.name)
    if (!parsed) continue
    const existing = grouped.get(parsed.base) ?? {
      recordId: recordIdFromBase(parsed.base),
      factorName: factorNameFromFileName(output.name),
      createdAt: new Date((output.mtime || 0) * 1000).toISOString(),
      createdAtNumber: output.mtime || 0,
      source: parsed.base.includes('vrun-') ? 'validation' : 'single',
      mode: 'phase1_only',
      conclusion: 'confirm',
      riskCount: 0,
      standardArtifacts: {},
      reviewArtifacts: {},
    }
    if ((output.mtime || 0) > (existing.createdAtNumber || 0)) {
      existing.createdAtNumber = output.mtime || 0
      existing.createdAt = new Date((output.mtime || 0) * 1000).toISOString()
    }
    const group = parsed.phase === 'standard' ? existing.standardArtifacts : existing.reviewArtifacts
    if (group) group[parsed.kind] = output.name
    existing.mode = existing.standardArtifacts?.md && existing.reviewArtifacts?.md
      ? 'one_click'
      : existing.reviewArtifacts?.md
        ? 'phase2_only'
        : 'phase1_only'
    grouped.set(parsed.base, existing)
  }

  return Array.from(grouped.values())
    .map(({ createdAtNumber: _createdAtNumber, ...record }) => record)
    .sort((a, b) => String(b.createdAt || '').localeCompare(String(a.createdAt || '')))
}

export function buildWorkbenchMetrics(history: Array<HistoryItem | ReviewHistoryRecord>) {
  const today = new Date()
  const isSameDay = (value?: number | string) => {
    if (!value) return false
    const date = typeof value === 'number' ? new Date(value * 1000) : new Date(value)
    return date.getFullYear() === today.getFullYear()
      && date.getMonth() === today.getMonth()
      && date.getDate() === today.getDate()
  }

  const todayItems = history.filter((item) => isSameDay(item.createdAt))
  return {
    total: todayItems.length,
    pass: todayItems.filter((item) => mapVerdictToConclusion(item.conclusion) === 'pass').length,
    fail: todayItems.filter((item) => mapVerdictToConclusion(item.conclusion) === 'fail').length,
    confirm: todayItems.filter((item) => mapVerdictToConclusion(item.conclusion) === 'confirm').length,
    suggestion: todayItems.filter((item) => mapVerdictToConclusion(item.conclusion) === 'suggestion').length,
  }
}

export function summarizeIssues(result?: ReviewResult | null) {
  const issues = result?.issues || []
  return {
    fail: issues.filter((item) => item.level === 'fail').length,
    confirm: issues.filter((item) => item.level === 'confirm').length,
    suggestion: issues.filter((item) => item.level === 'suggestion').length,
  }
}

export function normalizeConclusionFromReview(review: ReviewJsonPayload | null): string {
  return mapVerdictToConclusion(review?.conclusionBySystem || review?.conclusion)
}

export function normalizeStatusText(status?: string): string {
  const map: Record<string, string> = {
    queued: '排队中',
    running: '执行中',
    done: '已完成',
    error: '失败',
    cancelled: '已停止',
    cancelling: '停止中',
    skip: '已跳过',
  }
  return map[String(status || '').toLowerCase()] ?? (status || '-')
}

export function asArray(value: unknown): string[] {
  if (Array.isArray(value)) {
    return value.map((item) => String(item)).filter(Boolean)
  }
  return []
}

export function toMultilineText(value: unknown): string {
  if (Array.isArray(value)) return value.map((item) => String(item)).join('\n')
  if (value == null) return ''
  if (typeof value === 'object') return JSON.stringify(value, null, 2)
  return String(value)
}

export function normalizeReviewVerdict(value?: string): ReviewVerdict {
  const normalized = String(value || '').trim().toLowerCase()
  if (!normalized) return 'confirm'
  return normalized
}
