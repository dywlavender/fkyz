import { ReloadOutlined, StopOutlined } from '@ant-design/icons'
import { Alert, Button, Card, Descriptions, Progress, Space, Table, Tag, Typography } from 'antd'

import type { RegressionTaskItem, RegressionTaskResult, TaskRecord } from '../../../types/review'
import { conclusionLabel, isTerminalTask, taskStatusColor, taskStatusLabel } from '../model'

interface RegressionTaskCardProps {
  task: TaskRecord<RegressionTaskResult> | null
  taskId?: string
  recovering: boolean
  cancelling: boolean
  active: boolean
  onTerminate: () => void
  onRefresh: () => void
  onOpenResult: (item: RegressionTaskItem) => void
}

export function RegressionTaskCard({
  task,
  taskId,
  recovering,
  cancelling,
  active,
  onTerminate,
  onRefresh,
  onOpenResult,
}: RegressionTaskCardProps) {
  if (!task && !recovering) return null

  const items = task?.result?.items || []
  const summary = task?.result?.summary
  const total = summary?.total ?? task?.caseIds?.length ?? items.length
  const completed = (summary?.pass || 0) + (summary?.fail || 0) + (summary?.error || 0) + (summary?.skip || 0)
  const progress = total ? Math.min(100, Math.round((completed / total) * 100)) : 0

  return (
    <Card
      className="compact-card"
      title="最近一次回归验证任务"
      extra={(
        <Space size={8}>
          <Button danger size="small" icon={<StopOutlined />} loading={cancelling} disabled={!taskId || !active} onClick={onTerminate}>
            终止执行
          </Button>
          <Button size="small" icon={<ReloadOutlined />} loading={recovering} disabled={!taskId} onClick={onRefresh}>
            刷新进度
          </Button>
        </Space>
      )}
    >
      {task ? (
        <Space direction="vertical" size={12} style={{ width: '100%' }}>
          <Alert
            showIcon
            type={task.status === 'error' ? 'error' : isTerminalTask(task) ? 'success' : 'info'}
            message={(
              <Space wrap>
                <span>任务状态</span>
                <Tag color={taskStatusColor(task.status)}>{taskStatusLabel(task.status)}</Tag>
                <Typography.Text type="secondary">{summary?.currentTitle || ''}</Typography.Text>
              </Space>
            )}
            description={task.result?.stderr || (active ? '任务仍在后端执行，离开页面不会中断；返回验证集页面会自动恢复。' : '任务结果已保留，可查看每条案例的实际结论和报告。')}
          />
          <Descriptions bordered column={{ xs: 1, sm: 2, lg: 4 }} size="small">
            <Descriptions.Item label="Task ID">{task.id || '-'}</Descriptions.Item>
            <Descriptions.Item label="总数">{total}</Descriptions.Item>
            <Descriptions.Item label="通过">{summary?.pass || 0}</Descriptions.Item>
            <Descriptions.Item label="未通过">{(summary?.fail || 0) + (summary?.error || 0)}</Descriptions.Item>
          </Descriptions>
          <Progress
            percent={progress}
            status={task.status === 'error' ? 'exception' : task.status === 'done' ? 'success' : 'active'}
            format={() => `${completed} / ${total}`}
          />
          <Table<RegressionTaskItem>
            rowKey={(record, index) => record.id || `regression-${index}`}
            className="compact-table"
            size="small"
            pagination={{ pageSize: 5, size: 'small' }}
            dataSource={items}
            columns={[
              { title: '案例', dataIndex: 'title', ellipsis: true, render: (value?: string, record?: RegressionTaskItem) => value || record?.id || '-' },
              { title: '实际结论', dataIndex: 'verdict', width: 110, render: (value?: string) => value ? conclusionLabel(value) : '-' },
              {
                title: '期望匹配',
                width: 130,
                render: (_value, record) => {
                  const status = String(record.status || '').toLowerCase()
                  if (status === 'running') return <Tag color="processing">执行中</Tag>
                  if (status === 'error') return <Tag color="error">执行失败</Tag>
                  if (status === 'skip') return <Tag>已取消</Tag>
                  if (record.expectedOk || status === 'pass') return <Tag color="success">符合预期</Tag>
                  if (status === 'fail') return <Tag color="error">不符合预期</Tag>
                  return <Tag>待执行</Tag>
                },
              },
              {
                title: '结果',
                width: 100,
                render: (_value, record) => (
                  <Button type="link" size="small" disabled={!record.raw && !record.reviewFileId} onClick={() => onOpenResult(record)}>
                    查看
                  </Button>
                ),
              },
            ]}
          />
        </Space>
      ) : (
        <Typography.Text type="secondary">正在恢复最近一次验证任务...</Typography.Text>
      )}
    </Card>
  )
}
