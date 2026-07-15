import { Card, Col, Descriptions, Empty, Row, Space, Statistic, Tag, Typography } from 'antd'

import type { SystemHealth } from '../types/review'

interface SettingsProps {
  health?: SystemHealth | null
}

export function Settings({ health }: SettingsProps) {
  if (!health) {
    return (
      <Space className="app-page" direction="vertical" size={12} style={{ width: '100%' }}>
        <Card className="compact-card">
          <Empty description="暂无系统信息" />
        </Card>
      </Space>
    )
  }

  const checks = Object.entries(health.checks || {})
  const files = Object.entries(health.files || {})

  return (
    <Space className="app-page" direction="vertical" size={12} style={{ width: '100%' }}>
      <Card className="compact-card">
        <Space direction="vertical" size={2}>
          <Typography.Title level={5} style={{ margin: 0 }}>系统设置</Typography.Title>
          <Typography.Text type="secondary" style={{ fontSize: 12 }}>
            查看运行环境、Claude 调用状态、输出目录和基础文件检查结果。
          </Typography.Text>
        </Space>
      </Card>

      <Row gutter={[12, 12]}>
        <Col xs={12} md={6}><Card className="compact-card"><Statistic title="服务状态" value={health.ok ? '健康' : '需检查'} /></Card></Col>
        <Col xs={12} md={6}><Card className="compact-card"><Statistic title="运行模式" value={health.claude?.mock_mode ? 'Mock' : 'Claude'} /></Card></Col>
        <Col xs={12} md={6}><Card className="compact-card"><Statistic title="接口文档" value={health.api_docs?.count ?? 0} /></Card></Col>
        <Col xs={12} md={6}><Card className="compact-card"><Statistic title="文件检查" value={checks.filter(([, value]) => value).length} suffix={`/ ${checks.length}`} /></Card></Col>
      </Row>

      <Card className="compact-card" title="运行状态">
        <Descriptions bordered column={2} size="small">
          <Descriptions.Item label="运行模式">
            <Tag color={health.claude?.mock_mode ? 'warning' : 'processing'}>{health.claude?.mock_mode ? 'Mock' : 'Claude'}</Tag>
          </Descriptions.Item>
          <Descriptions.Item label="服务状态">
            <Tag color={health.ok ? 'success' : 'error'}>{health.ok ? '健康' : '需检查'}</Tag>
          </Descriptions.Item>
          <Descriptions.Item label="Claude 状态">
            {health.claude?.claude_path ? `可用：${health.claude.claude_path}` : '未检测到可执行命令'}
          </Descriptions.Item>
          <Descriptions.Item label="Claude 命令">
            {health.claude?.command || '-'}
          </Descriptions.Item>
          <Descriptions.Item label="Prompt / Skill 来源">
            <Typography.Text code>risk_review_web/prompts/</Typography.Text>
            {' 与 '}
            <Typography.Text code>risk_review_web/skills/risk_review/</Typography.Text>
          </Descriptions.Item>
          <Descriptions.Item label="输出目录">
            <Typography.Text code>outputs/</Typography.Text>
            {health.outputs?.writable ? <Tag color="success" style={{ marginLeft: 8 }}>可写</Tag> : <Tag color="error" style={{ marginLeft: 8 }}>不可写</Tag>}
          </Descriptions.Item>
          <Descriptions.Item label="离线依赖说明">
            前端轮询默认 10 分钟，后端以实际任务运行时间为准。
          </Descriptions.Item>
        </Descriptions>
      </Card>

      <Card className="compact-card" title="文件与依赖检查">
        {files.length || checks.length ? (
          <Descriptions bordered column={1} size="small">
            {checks.map(([key, value]) => (
              <Descriptions.Item key={key} label={key}>
                <Tag color={value ? 'success' : 'error'}>{value ? '通过' : '失败'}</Tag>
              </Descriptions.Item>
            ))}
            {files.map(([key, value]) => (
              <Descriptions.Item key={key} label={key}>
                <Space wrap>
                  <Typography.Text code>{value.path || '-'}</Typography.Text>
                  <Tag color={value.exists ? 'success' : 'error'}>{value.exists ? '存在' : '缺失'}</Tag>
                  {typeof value.size === 'number' ? <Tag>{value.size} B</Tag> : null}
                </Space>
              </Descriptions.Item>
            ))}
          </Descriptions>
        ) : (
          <Empty image={Empty.PRESENTED_IMAGE_SIMPLE} description="后端未返回文件检查明细" />
        )}
      </Card>
    </Space>
  )
}
