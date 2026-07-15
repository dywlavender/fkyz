import { Alert, Card, Col, Row, Statistic, Typography } from 'antd'

import type { ReviewResult } from '../types/review'
import { StatusBadge } from './StatusBadge'
import { summarizeIssues } from '../utils/reviewTransform'

interface ConclusionCardProps {
  result?: ReviewResult | null
}

export function ConclusionCard({ result }: ConclusionCardProps) {
  const stats = summarizeIssues(result)
  const summary = result?.summary || '暂无结构化总结。'

  return (
    <Card
      className="compact-card"
      title="评审结论"
      extra={<StatusBadge value={result?.riskLevel || result?.conclusion} />}
    >
      <Alert
        type={result?.conclusion === 'fail' ? 'error' : result?.conclusion === 'pass' ? 'success' : 'warning'}
        message={summary}
        showIcon
        style={{ marginBottom: 12 }}
      />
      <Row gutter={[12, 12]}>
        <Col xs={24} sm={8}>
          <Statistic title="不符合" value={stats.fail} valueStyle={{ color: '#c63c35' }} />
        </Col>
        <Col xs={24} sm={8}>
          <Statistic title="待确认" value={stats.confirm} valueStyle={{ color: '#d27a18' }} />
        </Col>
        <Col xs={24} sm={8}>
          <Statistic title="建议" value={stats.suggestion} valueStyle={{ color: '#7a8699' }} />
        </Col>
      </Row>
      {!!result?.majorRisks?.length && (
        <div style={{ marginTop: 12 }}>
          <Typography.Text strong>风险摘要</Typography.Text>
          <ul style={{ margin: '8px 0 0 18px', padding: 0 }}>
            {result.majorRisks.map((item) => (
              <li key={item}>{item}</li>
            ))}
          </ul>
        </div>
      )}
      {!!result?.suggestions?.length && (
        <div style={{ marginTop: 12 }}>
          <Typography.Text strong>建议动作</Typography.Text>
          <ul style={{ margin: '8px 0 0 18px', padding: 0 }}>
            {result.suggestions.map((item) => (
              <li key={item}>{item}</li>
            ))}
          </ul>
        </div>
      )}
    </Card>
  )
}
