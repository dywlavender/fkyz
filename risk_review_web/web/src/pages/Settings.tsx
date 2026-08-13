import { Alert, Button, Card, Col, Descriptions, Empty, Form, InputNumber, Row, Space, Statistic, Tag, Typography } from 'antd'
import { useEffect, useState } from 'react'

import { fetchRuntimeSettings, updateRuntimeSettings } from '../api/reviewApi'
import type { AppNotice, RuntimeSettings, SystemHealth } from '../types/review'

interface SettingsProps {
  health?: SystemHealth | null
  onNotice: (notice: AppNotice) => void
  onSettingsChanged?: () => Promise<void> | void
}

export function Settings({ health, onNotice, onSettingsChanged }: SettingsProps) {
  const [form] = Form.useForm<RuntimeSettings>()
  const [settingsLoading, setSettingsLoading] = useState(true)
  const [saving, setSaving] = useState(false)
  const globalConcurrency = Form.useWatch('globalConcurrency', form) || 1

  useEffect(() => {
    let active = true
    setSettingsLoading(true)
    fetchRuntimeSettings()
      .then((settings) => {
        if (active) form.setFieldsValue(settings)
      })
      .catch((caught) => {
        if (active) onNotice({ type: 'error', message: caught instanceof Error ? caught.message : '加载运行设置失败' })
      })
      .finally(() => {
        if (active) setSettingsLoading(false)
      })
    return () => {
      active = false
    }
  }, [form, onNotice])

  async function handleSave() {
    try {
      const values = await form.validateFields()
      setSaving(true)
      const settings = await updateRuntimeSettings(values)
      form.setFieldsValue(settings)
      await onSettingsChanged?.()
      onNotice({ type: 'success', message: '运行设置已生效' })
    } catch (caught) {
      if (caught instanceof Error) onNotice({ type: 'error', message: caught.message })
    } finally {
      setSaving(false)
    }
  }

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
  const agent = health.modelRuntime?.agent

  return (
    <Space className="app-page" direction="vertical" size={12} style={{ width: '100%' }}>
      <Card className="compact-card">
        <Space direction="vertical" size={2}>
          <Typography.Title level={5} style={{ margin: 0 }}>系统设置</Typography.Title>
          <Typography.Text type="secondary" style={{ fontSize: 12 }}>
            统一配置任务并发，并查看 Agent 运行环境与基础文件检查结果。
          </Typography.Text>
        </Space>
      </Card>

      <Row gutter={[12, 12]}>
        <Col xs={12} md={6}><Card className="compact-card"><Statistic title="服务状态" value={health.ok ? '健康' : '需检查'} /></Card></Col>
        <Col xs={12} md={6}><Card className="compact-card"><Statistic title="运行模式" value={health.mock_mode ? 'Mock' : 'LangChain Agent'} /></Card></Col>
        <Col xs={12} md={6}><Card className="compact-card"><Statistic title="接口文档" value={health.api_docs?.count ?? 0} /></Card></Col>
        <Col xs={12} md={6}><Card className="compact-card"><Statistic title="文件检查" value={checks.filter(([, value]) => value).length} suffix={`/ ${checks.length}`} /></Card></Col>
      </Row>

      <Card
        className="compact-card"
        title="运行设置"
        extra={<Button type="primary" loading={saving} disabled={settingsLoading} onClick={() => void handleSave()}>保存设置</Button>}
        loading={settingsLoading}
      >
        <Alert
          showIcon
          type="info"
          message="任务并发数由此处统一管理"
          description="设置只影响保存后新提交的任务；已排队或运行任务保持原有调度。"
          style={{ marginBottom: 16 }}
        />
        <Form form={form} layout="vertical">
          <Row gutter={[16, 0]}>
            <Col xs={24} md={8}>
              <Form.Item
                name="globalConcurrency"
                label="全局并发上限"
                extra="单后端进程内同时执行的任务总数上限。"
                rules={[{ required: true, message: '请输入全局并发上限' }]}
              >
                <InputNumber min={1} max={32} precision={0} style={{ width: '100%' }} />
              </Form.Item>
            </Col>
            <Col xs={24} md={8}>
              <Form.Item
                name="batchConcurrency"
                label="批量评审并发上限"
                extra="单个批量评审可同时占用的最大任务数。"
                dependencies={['globalConcurrency']}
                rules={[
                  { required: true, message: '请输入批量评审并发上限' },
                  ({ getFieldValue }) => ({
                    validator: (_rule, value) => !value || value <= getFieldValue('globalConcurrency')
                      ? Promise.resolve()
                      : Promise.reject(new Error('不能超过全局并发上限')),
                  }),
                ]}
              >
                <InputNumber min={1} max={globalConcurrency} precision={0} style={{ width: '100%' }} />
              </Form.Item>
            </Col>
            <Col xs={24} md={8}>
              <Form.Item
                name="scheduledConcurrency"
                label="定时任务并发上限"
                extra="单次定时运行可同时执行的因子数。"
                dependencies={['globalConcurrency']}
                rules={[
                  { required: true, message: '请输入定时任务并发上限' },
                  ({ getFieldValue }) => ({
                    validator: (_rule, value) => !value || value <= getFieldValue('globalConcurrency')
                      ? Promise.resolve()
                      : Promise.reject(new Error('不能超过全局并发上限')),
                  }),
                ]}
              >
                <InputNumber min={1} max={globalConcurrency} precision={0} style={{ width: '100%' }} />
              </Form.Item>
            </Col>
          </Row>
        </Form>
      </Card>

      <Card className="compact-card" title="运行状态">
        <Descriptions bordered column={2} size="small">
          <Descriptions.Item label="运行模式">
            <Tag color={health.mock_mode ? 'warning' : 'processing'}>{health.mock_mode ? 'Mock' : 'LangChain Agent'}</Tag>
          </Descriptions.Item>
          <Descriptions.Item label="服务状态">
            <Tag color={health.ok ? 'success' : 'error'}>{health.ok ? '健康' : '需检查'}</Tag>
          </Descriptions.Item>
          <Descriptions.Item label="Agent 状态">
            {agent?.available ? `可用：LangChain ${agent.langchain_version || '-'}` : agent?.note || '模型连接配置不完整'}
          </Descriptions.Item>
          <Descriptions.Item label="Agent 配置">
            {`${agent?.provider || '-'} / ${agent?.model || '-'} / ${agent?.python || '-'}`}
          </Descriptions.Item>
          <Descriptions.Item label="Prompt / Skill 来源">
            <Typography.Text code>risk_review_web/prompts/</Typography.Text>
            {' 与 '}
            <Typography.Text code>risk_review_web/prompts/risk-review-skill/</Typography.Text>
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
