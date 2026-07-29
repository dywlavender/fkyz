import { Card, Col, Row, Statistic } from 'antd'

import { formatDate } from '../model'

export interface ValidationStatsValue {
  total: number
  passed: number
  failed: number
  unreviewable: number
  agreementRate: number | null
  recent?: string
}

function CompactDateStat({ title, value }: { title: string; value?: string }) {
  return (
    <div className="compact-date-stat">
      <div className="compact-date-stat-title">{title}</div>
      <div className="compact-date-stat-value" title={formatDate(value)}>{formatDate(value)}</div>
    </div>
  )
}

export function ValidationStats({ value }: { value: ValidationStatsValue }) {
  return (
    <Row gutter={[12, 12]}>
      <Col xs={24} sm={12} xl={4}><Card className="compact-card"><Statistic title="案例总数" value={value.total} /></Card></Col>
      <Col xs={24} sm={12} xl={4}><Card className="compact-card"><Statistic title="最近通过" value={value.passed} /></Card></Col>
      <Col xs={24} sm={12} xl={4}><Card className="compact-card"><Statistic title="最近失败" value={value.failed} /></Card></Col>
      <Col xs={24} sm={12} xl={4}><Card className="compact-card"><Statistic title="无法评审" value={value.unreviewable} /></Card></Col>
      <Col xs={24} sm={12} xl={4}><Card className="compact-card"><Statistic title="一致率" value={value.agreementRate ?? '暂无'} suffix={value.agreementRate == null ? undefined : '%'} precision={1} /></Card></Col>
      <Col xs={24} sm={12} xl={4}><Card className="compact-card"><CompactDateStat title="最近执行时间" value={value.recent} /></Card></Col>
    </Row>
  )
}
