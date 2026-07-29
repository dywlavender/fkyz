import { Alert, Button, Form, Input, Select, Space, Tag, Typography } from 'antd'
import type { FormInstance } from 'antd'

import type { ApiDocItem } from '../../../types/review'
import { DEFAULT_VALUE_SEMANTICS } from '../constants'
import type { ReviewFormValues } from '../types'

interface SingleReviewInputPanelProps {
  form: FormInstance<ReviewFormValues>
  apiDocs: ApiDocItem[]
  loading: boolean
  executionDisabled: boolean
  oneClickRunning: boolean
  stoppingExecution: boolean
  hasExecutionLogs: boolean
  userStatus: string
  userStatusColor: string
  error?: string
  onLoadCase: () => void
  onClear: () => void
  onOpenApiDoc: () => void
  onStartReview: () => void
  onStopExecution: () => void
  onOpenExecutionLogs: () => void
  onValuesChange: (values: ReviewFormValues) => void
}

export function SingleReviewInputPanel(props: SingleReviewInputPanelProps) {
  return (
    <section className="work-surface single-input-surface" aria-label="评审输入">
      <div className="surface-heading">
        <div>
          <Typography.Title level={5}>评审输入</Typography.Title>
          <Typography.Text type="secondary">填写需求、接口信息和待评审内容。</Typography.Text>
        </div>
        <Space size={8}>
          <Button size="small" disabled={props.executionDisabled} onClick={props.onLoadCase}>加载案例</Button>
          <Button size="small" danger disabled={props.executionDisabled} onClick={props.onClear}>清空</Button>
        </Space>
      </div>
      <Form
        form={props.form}
        layout="vertical"
        initialValues={{ apiNames: [], defaultValues: DEFAULT_VALUE_SEMANTICS }}
        className="single-review-form"
        onValuesChange={(_, values) => props.onValuesChange(values as ReviewFormValues)}
      >
        <Form.Item name="factorName" label="因子名称" rules={[{ required: true, message: '请输入因子名称' }]}>
          <Input disabled={props.loading} placeholder="例如：近30日交易金额" />
        </Form.Item>
        <Form.Item
          name="apiNames"
          label={<Space size={8}><span>关联接口文档</span><Button type="link" size="small" onClick={props.onOpenApiDoc}>查看已选接口</Button></Space>}
          rules={[{ required: true, message: '至少选择一个接口文档' }]}
        >
          <Select
            disabled={props.loading}
            mode="multiple"
            showSearch
            optionFilterProp="label"
            placeholder="搜索并选择接口文档"
            options={props.apiDocs.map((item) => ({ label: `${item.name}${item.category ? ` · ${item.category}` : ''}`, value: item.name }))}
          />
        </Form.Item>
        <Form.Item name="requirement" label="需求内容" rules={[{ required: true, message: '请输入需求内容' }]}>
          <Input.TextArea disabled={props.loading} rows={4} placeholder="填写业务背景、使用场景和加工逻辑要求" />
        </Form.Item>
        <div className="review-content-fieldset">
          <div className="review-content-fieldset-title">待评审内容</div>
          <Form.Item name="paramFields" label="因子加工输入字段"><Input.TextArea disabled={props.loading} rows={3} placeholder="每行一个字段，建议格式：字段名 | 字段来源 | 用途" /></Form.Item>
          <Form.Item name="procLogic" label="加工逻辑" rules={[{ required: true, message: '请输入加工逻辑' }]}><Input.TextArea disabled={props.loading} rows={6} placeholder="逐步填写待评审加工逻辑" /></Form.Item>
          <Form.Item name="defaultValues" label="默认值"><Input.TextArea disabled={props.loading} rows={2} placeholder="填写字段异常、无匹配记录等情况下的默认返回规则" /></Form.Item>
        </div>
        <div className="single-primary-actions">
          <Button size="large" type="primary" loading={props.oneClickRunning} disabled={props.executionDisabled && !props.oneClickRunning} onClick={props.onStartReview}>
            {props.oneClickRunning ? '评审中...' : '开始评审'}
          </Button>
          {props.loading ? <Button danger loading={props.stoppingExecution} onClick={props.onStopExecution}>终止执行</Button> : null}
          <Button disabled={!props.hasExecutionLogs} onClick={props.onOpenExecutionLogs}>执行详情</Button>
          <Tag color={props.userStatusColor}>{props.userStatus}</Tag>
        </div>
      </Form>
      {props.error ? (
        <Alert className="single-inline-error" type="error" showIcon message="评审执行失败" description={props.error} action={<Button size="small" onClick={props.onOpenExecutionLogs}>查看执行详情</Button>} />
      ) : null}
    </section>
  )
}
