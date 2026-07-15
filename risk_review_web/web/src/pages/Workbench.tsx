import {
  ArrowRightOutlined,
  AuditOutlined,
  CheckCircleOutlined,
  ClockCircleOutlined,
  FileSearchOutlined,
  ScheduleOutlined,
  WarningOutlined,
} from '@ant-design/icons'
import { Button, Card, Col, Row, Space, Statistic, Table, Tag, Typography } from 'antd'
import type { ColumnsType } from 'antd/es/table'

import type { ReviewHistoryRecord } from '../types/review'
import { buildWorkbenchMetrics, formatDateTimeValue } from '../utils/reviewTransform'
import { EmptyState } from '../components/EmptyState'
import { StatusBadge } from '../components/StatusBadge'

interface WorkbenchProps {
  history: ReviewHistoryRecord[]
  onOpenHistory: (item: ReviewHistoryRecord) => void
  onNavigateSingle: () => void
  onNavigateBatch: () => void
  onNavigateScheduled: () => void
  onNavigateValidation: () => void
  onNavigateHistory: () => void
}

export function Workbench(props: WorkbenchProps) {
  const {
    history,
    onOpenHistory,
    onNavigateSingle,
    onNavigateBatch,
    onNavigateScheduled,
    onNavigateValidation,
    onNavigateHistory,
  } = props
  const metrics = buildWorkbenchMetrics(history)
  const reviewed = metrics.pass + metrics.fail + metrics.confirm + metrics.suggestion
  const passRate = reviewed ? Math.round((metrics.pass / reviewed) * 100) : 0
  const recentRiskItems = history
    .filter((item) => item.conclusion === 'fail' || item.conclusion === 'confirm' || (item.riskCount ?? 0) > 0)
    .slice(0, 4)

  const columns: ColumnsType<ReviewHistoryRecord> = [
    { title: '因子名称', dataIndex: 'factorName', ellipsis: true },
    { title: '结论', dataIndex: 'conclusion', width: 120, render: (value: ReviewHistoryRecord['conclusion']) => <StatusBadge value={value || 'confirm'} /> },
    { title: '风险数', dataIndex: 'riskCount', width: 90, render: (value: ReviewHistoryRecord['riskCount']) => value ?? 0 },
    { title: '创建时间', dataIndex: 'createdAt', width: 180, render: (value: ReviewHistoryRecord['createdAt']) => formatDateTimeValue(value) },
    {
      title: '文档状态',
      width: 130,
      render: (_: unknown, record: ReviewHistoryRecord) => (
        <Space size={4} wrap>
          <Tag color={record.standardArtifacts?.md || record.standardArtifacts?.json ? 'blue' : 'default'}>准出{record.standardArtifacts?.md || record.standardArtifacts?.json ? '有' : '无'}</Tag>
          <Tag color={record.reviewArtifacts?.md || record.reviewArtifacts?.json ? 'green' : 'default'}>评审{record.reviewArtifacts?.md || record.reviewArtifacts?.json ? '有' : '无'}</Tag>
        </Space>
      ),
    },
    {
      title: '操作',
      width: 100,
      render: (_: unknown, record: ReviewHistoryRecord) => (
        <Button
          type="link"
          onClick={(event) => {
            event.stopPropagation()
            onOpenHistory(record)
          }}
        >
          查看
        </Button>
      ),
    },
  ]

  const workflowItems = [
    {
      title: '单因子评审',
      description: '生成准出标准并立即执行加工口径评审',
      icon: <FileSearchOutlined />,
      action: '开始评审',
      onClick: onNavigateSingle,
      primary: true,
    },
    {
      title: '批量评审',
      description: '导入 CSV / Excel，按并发队列批量处理',
      icon: <AuditOutlined />,
      action: '导入批量',
      onClick: onNavigateBatch,
    },
    {
      title: '定时任务',
      description: '维护周期性任务和运行记录',
      icon: <ScheduleOutlined />,
      action: '配置计划',
      onClick: onNavigateScheduled,
    },
    {
      title: '验证集',
      description: '执行回归案例，核对期望与实际结论',
      icon: <AuditOutlined />,
      action: '进入验证',
      onClick: onNavigateValidation,
    },
  ]

  return (
    <Space className="app-page workbench-page" direction="vertical" size={14} style={{ width: '100%' }}>
      <Row gutter={[12, 12]}>
        <Col xs={12} md={6}>
          <Card className="compact-card metric-card metric-card-success">
            <Statistic prefix={<CheckCircleOutlined />} title="通过数" value={metrics.pass} />
          </Card>
        </Col>
        <Col xs={12} md={6}>
          <Card className="compact-card metric-card metric-card-warning">
            <Statistic prefix={<WarningOutlined />} title="不符合/待确认" value={metrics.fail + metrics.confirm} />
          </Card>
        </Col>
        <Col xs={12} md={6}>
          <Card className="compact-card metric-card">
            <Statistic prefix={<ClockCircleOutlined />} title="总评审" value={metrics.total} />
          </Card>
        </Col>
        <Col xs={12} md={6}>
          <Card className="compact-card metric-card">
            <Statistic title="通过率" value={passRate} suffix="%" />
          </Card>
        </Col>
      </Row>

      <Row gutter={[12, 12]}>
        <Col xs={24} xl={9}>
          <Card className="compact-card" title="评审流程入口">
            <Space direction="vertical" size={10} style={{ width: '100%' }}>
              {workflowItems.map((item) => (
                <button
                  key={item.title}
                  type="button"
                  className={item.primary ? 'workflow-tile workflow-tile-primary' : 'workflow-tile'}
                  onClick={item.onClick}
                >
                  <span className="workflow-icon">{item.icon}</span>
                  <span className="workflow-copy">
                    <strong>{item.title}</strong>
                    <span>{item.description}</span>
                  </span>
                  <span className="workflow-action">
                    {item.action}
                    <ArrowRightOutlined />
                  </span>
                </button>
              ))}
            </Space>
          </Card>
          <Card className="compact-card" title="待关注记录">
            {recentRiskItems.length ? (
              <Space direction="vertical" size={8} style={{ width: '100%' }}>
                {recentRiskItems.map((item) => (
                  <button key={item.recordId} type="button" className="risk-row" onClick={() => onOpenHistory(item)}>
                    <span>
                      <strong>{item.factorName || item.recordId}</strong>
                      <Typography.Text type="secondary">{formatDateTimeValue(item.createdAt)}</Typography.Text>
                    </span>
	                    <StatusBadge value={item.conclusion || 'confirm'} />
                  </button>
                ))}
              </Space>
            ) : (
              <EmptyState title="暂无需要关注的风险记录。" />
            )}
          </Card>
        </Col>
        <Col xs={24} xl={15}>
          <Card
            className="compact-card table-console-card"
            title="最近评审"
            extra={<Typography.Text type="secondary">点击整行查看详情</Typography.Text>}
          >
            {history.length ? (
              <Table
                rowKey="recordId"
                dataSource={history.slice(0, 8)}
                columns={columns}
                className="compact-table"
                size="small"
                pagination={false}
                onRow={(record) => ({
                  onClick: () => onOpenHistory(record),
                })}
                scroll={{ x: 860 }}
              />
            ) : (
              <EmptyState title="历史接口暂无可用统计，工作台先展示空状态。" actionLabel="前往单因子评审" onAction={onNavigateSingle} />
            )}
          </Card>
        </Col>
      </Row>
    </Space>
  )
}
