import { Alert, Button, Card, Col, Descriptions, Drawer, Empty, Row, Space, Tabs, Tag, Timeline, Typography } from 'antd'
import type { ReactNode } from 'react'
import { useMemo, useRef, useState } from 'react'

import type { Phase1Response, Phase1Standard, Phase2Response, ReviewIssue, ReviewResult, RuleCheck, StandardStep, StepCheck } from '../types/review'
import { mapVerdictToConclusion } from '../utils/reviewTransform'
import { ArtifactPanel } from './ArtifactPanel'
import { EmptyState } from './EmptyState'
import { RuleCheckTable } from './RuleCheckTable'
import { StatusBadge } from './StatusBadge'
import { StepCheckTable } from './StepCheckTable'

interface ReviewResultPanelProps {
  result: ReviewResult
  standard?: Phase1Standard | null
  phase1?: Phase1Response | null
  phase2?: Phase2Response | null
  onOpenArtifact?: (fileId: string) => void
}

interface DiffItem {
  id: string
  type: string
  severity: 'high' | 'medium' | 'low'
  description: string
  source: '规则' | '步骤'
  relatedId?: string
}

const DIFF_TYPE_LABELS: Record<string, string> = {
  field_missing: '字段缺失',
  filter_missing: '过滤条件缺失',
  period_mismatch: '统计周期不一致',
  aggregation_mismatch: '聚合口径不一致',
  default_missing: '默认值策略缺失',
  output_mapping_error: '输出映射异常',
  evidence_missing: '证据不足',
  other: '其他差异',
}

const SEVERITY_COLOR: Record<DiffItem['severity'], string> = {
  high: 'red',
  medium: 'orange',
  low: 'blue',
}

const STEP_BORDER: Record<string, string> = {
  pass: '#52c41a',
  fail: '#ff4d4f',
  confirm: '#faad14',
  suggestion: '#8c8c8c',
}

function issueSummary(result: ReviewResult) {
  const summary = { fail: 0, confirm: 0, suggestion: 0 }
  for (const issue of result.issues || []) {
    const level = String(issue.level || '').trim().toLowerCase()
    if (['fail', 'failed', 'high', '不符合'].includes(level)) summary.fail += 1
    else if (['confirm', 'medium', '待确认'].includes(level)) summary.confirm += 1
    else if (['suggestion', 'suggest', 'low', 'info', '建议'].includes(level)) summary.suggestion += 1
  }
  if (result.issues?.length) return summary

  for (const item of [...(result.ruleChecks || []), ...(result.stepChecks || [])]) {
    const conclusion = mapVerdictToConclusion(item.verdict || item.conclusion)
    if (conclusion === 'fail') summary.fail += 1
    else if (conclusion === 'confirm') summary.confirm += 1
    else if (conclusion === 'suggestion') summary.suggestion += 1
  }
  return summary
}

function textFromEvidence(value?: string | string[]) {
  if (Array.isArray(value)) return value.join('\n')
  return value || '-'
}

function normalizeSeverity(level?: string): DiffItem['severity'] {
  const normalized = String(level || '').toLowerCase()
  if (['high', 'fail', 'fatal', '严重'].includes(normalized)) return 'high'
  if (['low', 'suggestion', 'info', '低'].includes(normalized)) return 'low'
  return 'medium'
}

function issueDescription(issue: ReviewIssue) {
  return issue.message || issue.impact || issue.suggestion || issue.actual || issue.standard || '未提供差异说明'
}

function issueType(issue: ReviewIssue) {
  const text = `${issue.dimension || ''} ${issue.message || ''} ${issue.impact || ''}`.toLowerCase()
  if (/字段|field/.test(text)) return 'field_missing'
  if (/过滤|filter/.test(text)) return 'filter_missing'
  if (/周期|period|window/.test(text)) return 'period_mismatch'
  if (/聚合|aggregate|sum|count|max|min/.test(text)) return 'aggregation_mismatch'
  if (/默认|default|空值|null/.test(text)) return 'default_missing'
  if (/输出|mapping/.test(text)) return 'output_mapping_error'
  if (/证据|evidence/.test(text)) return 'evidence_missing'
  return 'other'
}

function buildDiffItems(result: ReviewResult): DiffItem[] {
  const fromIssues = (result.issues || []).map((issue, index) => ({
    id: issue.id || `issue-${index}`,
    type: issueType(issue),
    severity: normalizeSeverity(issue.level),
    description: issueDescription(issue),
    source: issue.stepNo ? '步骤' as const : '规则' as const,
    relatedId: issue.ruleId || (issue.stepNo ? `Step ${issue.stepNo}` : issue.relatedRuleIds?.join('、')),
  }))
  if (fromIssues.length) return fromIssues

  const fromRules = (result.ruleChecks || [])
    .filter((item) => mapVerdictToConclusion(item.verdict || item.conclusion) !== 'pass')
    .map((item, index) => ({
      id: item.ruleId || `rule-${index}`,
      type: item.actualEvidence?.length || item.evidence ? 'other' : 'evidence_missing',
      severity: mapVerdictToConclusion(item.verdict || item.conclusion) === 'fail' ? 'high' as const : 'medium' as const,
      description: item.reason || item.risk || item.suggestion || item.standard || item.rule || '规则检查未通过',
      source: '规则' as const,
      relatedId: item.ruleId,
    }))
  const fromSteps = (result.stepChecks || [])
    .filter((item) => mapVerdictToConclusion(item.verdict || item.conclusion) !== 'pass')
    .map((item, index) => ({
      id: item.id || `step-${item.stepNo || index}`,
      type: item.actualEvidence?.length || item.evidence ? 'other' : 'evidence_missing',
      severity: mapVerdictToConclusion(item.verdict || item.conclusion) === 'fail' ? 'high' as const : 'medium' as const,
      description: item.reason || item.risk || item.suggestion || item.standardStep || item.step || '加工步骤检查未通过',
      source: '步骤' as const,
      relatedId: item.stepNo ? `Step ${item.stepNo}` : undefined,
    }))
  return [...fromRules, ...fromSteps]
}

function buildRiskSummaries(result: ReviewResult) {
  const issueLines = (result.issues || [])
    .filter((issue) => ['fail', 'confirm'].includes(String(issue.level || '').trim().toLowerCase()))
    .map(issueDescription)
  if (result.issues?.length) return issueLines.filter(Boolean)

  const stepLines = (result.stepChecks || [])
    .filter((item) => ['fail', 'confirm'].includes(mapVerdictToConclusion(item.verdict || item.conclusion)))
    .map((item) => item.reason || item.risk || item.standardStep || `Step ${item.stepNo || '-'} 需要确认`)
  const ruleLines = (result.ruleChecks || [])
    .filter((item) => ['fail', 'confirm'].includes(mapVerdictToConclusion(item.verdict || item.conclusion)))
    .map((item) => item.reason || item.risk || item.standard || `规则 ${item.ruleId || '-'} 需要确认`)
  return [...issueLines, ...stepLines, ...ruleLines].filter(Boolean)
}

function stepCheckFor(step: StandardStep, checks?: StepCheck[]) {
  return (checks || []).find((item) => item.stepNo === step.stepNo)
}

function standardRulesById(standard?: Phase1Standard | null) {
  const map = new Map<string, string>()
  for (const rule of standard?.rules || []) {
    if (rule.ruleId) map.set(rule.ruleId, rule.content || rule.dimension || rule.ruleId)
  }
  return map
}

function DiffSummaryCard({ items }: { items: DiffItem[] }) {
  if (!items.length) return <EmptyState title="暂未发现明显加工口径差异" />
  return (
    <Row gutter={[12, 12]}>
      {items.map((item) => (
        <Col xs={24} lg={12} key={item.id}>
          <Card size="small" style={{ borderRadius: 6 }}>
            <Space direction="vertical" size={8} style={{ width: '100%' }}>
              <Space wrap>
                <Tag>{DIFF_TYPE_LABELS[item.type] || DIFF_TYPE_LABELS.other}</Tag>
                <Tag color={SEVERITY_COLOR[item.severity]}>{item.severity}</Tag>
                <Tag>{item.source}</Tag>
                {item.relatedId ? <Tag>{item.relatedId}</Tag> : null}
              </Space>
              <Typography.Paragraph style={{ marginBottom: 0 }}>{item.description}</Typography.Paragraph>
            </Space>
          </Card>
        </Col>
      ))}
    </Row>
  )
}

function EvidenceText({ title, children }: { title: string; children?: ReactNode }) {
  return (
    <Space direction="vertical" size={4} style={{ width: '100%' }}>
      <Typography.Text type="secondary">{title}</Typography.Text>
      <Typography.Paragraph style={{ whiteSpace: 'pre-wrap', marginBottom: 0 }}>{children || '-'}</Typography.Paragraph>
    </Space>
  )
}

export function ReviewResultPanel({ result, standard, phase1, phase2, onOpenArtifact }: ReviewResultPanelProps) {
  const [selectedStep, setSelectedStep] = useState<StandardStep | StepCheck | null>(null)
  const [selectedRuleId, setSelectedRuleId] = useState<string>()
  const [activeTab, setActiveTab] = useState('overview')
  const riskListRef = useRef<HTMLDivElement | null>(null)
  const reviewIssueSummary = issueSummary(result)
  const diffItems = useMemo(() => buildDiffItems(result), [result])
  const riskSummaries = useMemo(() => buildRiskSummaries(result), [result])
  const rules = standardRulesById(standard)
  const standardSteps = standard?.processingLogic?.steps || []
  const selectedRule = (result.ruleChecks || []).find((item) => item.ruleId === selectedRuleId) || result.ruleChecks?.[0]
  const activeRuleId = selectedRuleId || selectedRule?.ruleId
  const relatedSteps = (result.stepChecks || []).filter((item) => activeRuleId && item.relatedRuleIds?.includes(activeRuleId))
  const unlinkedSteps = (result.stepChecks || []).filter((item) => !item.relatedRuleIds?.length)

  function openStep(stepNo?: number) {
    const standardStep = standardSteps.find((item) => item.stepNo === stepNo)
    const check = (result.stepChecks || []).find((item) => item.stepNo === stepNo)
    setSelectedStep(standardStep || check || null)
  }

  function showRiskDetails() {
    setActiveTab('overview')
    window.setTimeout(() => {
      riskListRef.current?.scrollIntoView({ behavior: 'smooth', block: 'start' })
    }, 0)
  }

  const topCards = [
    { title: '总体结论', value: <StatusBadge value={result.conclusion || 'confirm'} /> },
    { title: '不符合', value: `${reviewIssueSummary.fail}` },
    { title: '待确认', value: `${reviewIssueSummary.confirm}` },
    { title: '建议', value: `${reviewIssueSummary.suggestion}` },
  ]

  return (
    <Space direction="vertical" size={12} style={{ width: '100%' }}>
      <Row gutter={[12, 12]}>
        {topCards.map((item) => (
          <Col xs={24} sm={12} lg={6} key={item.title}>
            <Card size="small" style={{ borderRadius: 6, minHeight: 76 }}>
              <Space direction="vertical" size={6}>
                <Typography.Text type="secondary">{item.title}</Typography.Text>
                <Typography.Title level={4} style={{ margin: 0 }}>{item.value}</Typography.Title>
              </Space>
            </Card>
          </Col>
        ))}
      </Row>

      {riskSummaries.length ? (
        <Alert
          type={result.conclusion === 'fail' ? 'error' : 'warning'}
          showIcon
          message="需处理问题"
          description={(
            <Space direction="vertical" size={4}>
              {riskSummaries.slice(0, 3).map((item, index) => <Typography.Text key={`${item}-${index}`}>{index + 1}. {item}</Typography.Text>)}
              {riskSummaries.length > 3 ? (
                <Button type="link" size="small" style={{ padding: 0, height: 'auto' }} onClick={showRiskDetails}>
                  还有 {riskSummaries.length - 3} 条问题，查看详情
                </Button>
              ) : null}
            </Space>
          )}
        />
      ) : null}

      <Tabs
        activeKey={activeTab}
        onChange={setActiveTab}
        items={[
          {
            key: 'overview',
            label: '总览',
            children: (
              <Space direction="vertical" size={12} style={{ width: '100%' }}>
                <Card size="small" title="结论摘要">
                  <Typography.Paragraph style={{ marginBottom: 0 }}>{result.summary || '暂无结论摘要'}</Typography.Paragraph>
                </Card>
                <Card size="small" title="加工口径差异摘要"><DiffSummaryCard items={diffItems} /></Card>
                <Card size="small" title="需处理问题列表" ref={riskListRef}>
                  {riskSummaries.length ? (
                    <ul style={{ margin: 0, paddingLeft: 18 }}>{riskSummaries.map((item, index) => <li key={`${item}-${index}`}>{item}</li>)}</ul>
                  ) : <Empty image={Empty.PRESENTED_IMAGE_SIMPLE} description="暂无需处理问题" />}
                </Card>
                <Card size="small" title="建议处理动作">
                  {result.suggestions?.length ? (
                    <ul style={{ margin: 0, paddingLeft: 18 }}>{result.suggestions.map((item) => <li key={item}>{item}</li>)}</ul>
                  ) : <Empty image={Empty.PRESENTED_IMAGE_SIMPLE} description="暂无建议处理动作" />}
                </Card>
              </Space>
            ),
          },
          {
            key: 'flow',
            label: '加工链路',
            children: standardSteps.length ? (
              <Space direction="vertical" size={12} style={{ width: '100%' }}>
                {!result.stepChecks?.length ? <Alert showIcon type="info" message="暂无加工步骤评审结果，仅展示标准链路" /> : null}
                <Timeline
                  items={standardSteps.map((step) => {
                    const check = stepCheckFor(step, result.stepChecks)
                    const conclusion = mapVerdictToConclusion(check?.verdict || check?.conclusion)
                    const borderColor = STEP_BORDER[conclusion] || '#d9d9d9'
                    return {
                      color: borderColor,
                      children: (
                        <Card
                          size="small"
                          hoverable
                          onClick={() => setSelectedStep(step)}
                          style={{ borderColor, borderStyle: check ? 'solid' : 'dashed', borderRadius: 6 }}
                        >
                          <Space direction="vertical" size={8} style={{ width: '100%' }}>
                            <Space wrap>
                              <Typography.Text strong>Step {step.stepNo || '-'}</Typography.Text>
                              <Tag>{step.type || check?.stepType || '-'}</Tag>
                              <StatusBadge value={check?.verdict || check?.conclusion || 'confirm'} />
                            </Space>
                            <Typography.Text>{step.description || check?.standardStep || '暂无步骤说明'}</Typography.Text>
                            <Descriptions size="small" column={{ xs: 1, sm: 2 }}>
                              <Descriptions.Item label="因子加工输入字段">{step.inputFields?.join('、') || '-'}</Descriptions.Item>
                              <Descriptions.Item label="步骤产出字段">{step.outputField || '-'}</Descriptions.Item>
                            </Descriptions>
                          </Space>
                        </Card>
                      ),
                    }
                  })}
                />
              </Space>
            ) : <EmptyState title="暂无加工步骤标准，无法展示加工链路" />,
          },
          {
            key: 'evidence',
            label: '证据对照',
            children: result.ruleChecks?.length ? (
              <Space direction="vertical" size={12} style={{ width: '100%' }}>
                <Row gutter={[12, 12]}>
                  <Col xs={24} lg={7}>
                    <Card size="small" title="规则列表">
                      <Space direction="vertical" size={8} style={{ width: '100%' }}>
                        {(result.ruleChecks || []).map((rule: RuleCheck, index) => {
                          const id = rule.ruleId || `rule-${index}`
                          return (
                            <Card
                              size="small"
                              hoverable
                              key={id}
                              onClick={() => setSelectedRuleId(rule.ruleId)}
                              style={{ borderColor: activeRuleId === rule.ruleId ? '#1677ff' : undefined, borderRadius: 6 }}
                            >
                              <Space direction="vertical" size={6}>
                                <Space wrap><Tag>{rule.ruleId || '-'}</Tag><StatusBadge value={rule.verdict || rule.conclusion} /></Space>
                                <Typography.Paragraph ellipsis={{ rows: 2 }} style={{ marginBottom: 0 }}>{rule.standard || rule.rule || '-'}</Typography.Paragraph>
                              </Space>
                            </Card>
                          )
                        })}
                      </Space>
                    </Card>
                  </Col>
                  <Col xs={24} lg={7}>
                    <Card size="small" title="关联步骤">
                      {relatedSteps.length ? (
                        <Space direction="vertical" size={8} style={{ width: '100%' }}>
                          {relatedSteps.map((step) => (
                            <Card size="small" hoverable key={`${step.stepNo}-${step.stepType}`} onClick={() => openStep(step.stepNo)} style={{ borderRadius: 6 }}>
                              <Space direction="vertical" size={6}>
                                <Space wrap><Tag>Step {step.stepNo || '-'}</Tag><Tag>{step.stepType || '-'}</Tag><StatusBadge value={step.verdict || step.conclusion} /></Space>
                                <Typography.Paragraph ellipsis={{ rows: 2 }} style={{ marginBottom: 0 }}>{step.standardStep || step.step || '-'}</Typography.Paragraph>
                              </Space>
                            </Card>
                          ))}
                        </Space>
                      ) : <Alert type="warning" showIcon message="该规则未关联到任何加工步骤，可能存在规则未落地风险" />}
                    </Card>
                  </Col>
                  <Col xs={24} lg={10}>
                    <Card size="small" title="证据详情">
                      {selectedRule ? (
                        <Space direction="vertical" size={12} style={{ width: '100%' }}>
                          <EvidenceText title="标准要求">{selectedRule.standard || selectedRule.rule || (activeRuleId ? rules.get(activeRuleId) : '')}</EvidenceText>
                          <EvidenceText title="实现证据">{textFromEvidence(selectedRule.actualEvidence || (selectedRule.evidence ? [selectedRule.evidence] : []))}</EvidenceText>
                          <EvidenceText title="评审原因">{selectedRule.reason || '-'}</EvidenceText>
                          <EvidenceText title="风险说明">{selectedRule.risk || selectedRule.suggestion || '-'}</EvidenceText>
                        </Space>
                      ) : <Empty image={Empty.PRESENTED_IMAGE_SIMPLE} description="请选择规则" />}
                    </Card>
                  </Col>
                </Row>
                {unlinkedSteps.length ? (
                  <Alert
                    showIcon
                    type="warning"
                    message="以下加工步骤缺少规则依据"
                    description={unlinkedSteps.map((step) => `Step ${step.stepNo || '-'}：${step.standardStep || step.step || '-'}`).join('\n')}
                  />
                ) : null}
              </Space>
            ) : <EmptyState title="暂无规则检查结果，无法展示证据对照" />,
          },
          {
            key: 'rule-checks',
            label: '规则检查',
            children: <RuleCheckTable items={result.ruleChecks} />,
          },
          {
            key: 'step-checks',
            label: '步骤检查',
            children: <StepCheckTable items={result.stepChecks} onStepClick={openStep} />,
          },
          {
            key: 'artifacts',
            label: '原始产物',
            children: (
              <ArtifactPanel
                standardFileId={phase1?.standardFileId}
                standardJsonFileId={phase1?.standardJsonFileId || phase1?.standardSummaryFileId}
                standardRawFileId={phase1?.standardRawFileId}
                standardValidationFileId={phase1?.standardValidationFileId}
                reviewFileId={phase2?.reviewFileId}
                reviewJsonFileId={phase2?.reviewJsonFileId}
                reviewRawFileId={phase2?.reviewRawFileId}
                reviewValidationFileId={phase2?.reviewValidationFileId}
                onOpen={onOpenArtifact}
              />
            ),
          },
        ]}
      />

      <Drawer className="drawer-compact" title="步骤详情" width="min(720px, 90vw)" open={!!selectedStep} onClose={() => setSelectedStep(null)}>
        {selectedStep ? (() => {
          const stepNo = 'stepNo' in selectedStep ? selectedStep.stepNo : undefined
          const standardStep = standardSteps.find((item) => item.stepNo === stepNo)
          const check = (result.stepChecks || []).find((item) => item.stepNo === stepNo) || ('verdict' in selectedStep ? selectedStep as StepCheck : undefined)
          const relatedRuleIds = check?.relatedRuleIds?.length ? check.relatedRuleIds : standardStep?.basisRuleIds || []
          return (
            <Space direction="vertical" size={12} style={{ width: '100%' }}>
              <Descriptions bordered size="small" column={1}>
                <Descriptions.Item label="步骤号">Step {stepNo || '-'}</Descriptions.Item>
                <Descriptions.Item label="加工类型">{standardStep?.type || check?.stepType || '-'}</Descriptions.Item>
                <Descriptions.Item label="结论"><StatusBadge value={check?.verdict || check?.conclusion || 'confirm'} /></Descriptions.Item>
                <Descriptions.Item label="因子加工输入字段">{standardStep?.inputFields?.join('、') || '-'}</Descriptions.Item>
                <Descriptions.Item label="步骤产出字段">{standardStep?.outputField || '-'}</Descriptions.Item>
                <Descriptions.Item label="关联规则">
                  {relatedRuleIds.length ? <Space wrap>{relatedRuleIds.map((id) => <Tag key={id}>{id}</Tag>)}</Space> : '未关联规则'}
                </Descriptions.Item>
              </Descriptions>
              <EvidenceText title="标准步骤">{standardStep?.description || check?.standardStep || check?.step || '-'}</EvidenceText>
              <EvidenceText title="实现证据">{textFromEvidence(check?.actualEvidence || (check?.evidence ? [check.evidence] : []))}</EvidenceText>
              <EvidenceText title="评审原因">{check?.reason || '-'}</EvidenceText>
            </Space>
          )
        })() : null}
      </Drawer>
    </Space>
  )
}
