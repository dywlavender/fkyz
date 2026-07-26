import { Button, Drawer, Empty, List, Progress, Space, Typography } from 'antd'

import type { TaskRecord } from '../types/review'
import { TaskStatusBadge } from './TaskStatusBadge'

interface TaskCenterDrawerProps {
  open: boolean
  tasks: TaskRecord[]
  onClose: () => void
  onCancel: (taskId: string) => void
  onOpenTask: (task: TaskRecord) => void
}

function taskTitle(task: TaskRecord) {
  return task.context?.taskName
    || task.context?.factorName
    || ({
      phase1: '生成标准',
      phase2: '单因子评审',
      batch: '批量评审',
      regression: '质量验证',
      'review-mode-experiment': '模式对比实验',
    }[String(task.taskType || '')])
    || '评审任务'
}

function taskProgress(task: TaskRecord) {
  const result = task.result as { items?: Array<{ status?: string }> } | undefined
  const items = result?.items || []
  if (!items.length) return undefined
  const finished = items.filter((item) => ['done', 'pass', 'fail', 'error', 'skip'].includes(String(item.status || ''))).length
  return Math.round((finished / items.length) * 100)
}

export function TaskCenterDrawer(props: TaskCenterDrawerProps) {
  const { open, tasks, onClose, onCancel, onOpenTask } = props
  const sorted = [...tasks].sort((left, right) => (right.updated || right.created || 0) - (left.updated || left.created || 0))

  return (
    <Drawer title="任务中心" width={520} open={open} onClose={onClose}>
      {sorted.length ? (
        <List
          className="task-center-list"
          dataSource={sorted}
          renderItem={(task) => {
            const progress = taskProgress(task)
            const active = ['queued', 'running', 'cancelling'].includes(String(task.status || ''))
            return (
              <List.Item
                actions={[
                  <Button key="view" type="link" onClick={() => onOpenTask(task)}>查看</Button>,
                  <Button
                    key="cancel"
                    danger
                    type="link"
                    disabled={!active || !task.id}
                    onClick={() => task.id && onCancel(task.id)}
                  >
                    终止
                  </Button>,
                ]}
              >
                <List.Item.Meta
                  title={(
                    <Space size={8}>
                      <Typography.Text strong>{taskTitle(task)}</Typography.Text>
                      <TaskStatusBadge value={task.status} />
                    </Space>
                  )}
                  description={(
                    <Space direction="vertical" size={4} style={{ width: '100%' }}>
                      <Typography.Text type="secondary">
                        {task.taskType || '任务'} · {task.id?.slice(0, 8) || '-'}
                      </Typography.Text>
                      {progress !== undefined ? <Progress percent={progress} size="small" /> : null}
                    </Space>
                  )}
                />
              </List.Item>
            )
          }}
        />
      ) : (
        <Empty image={Empty.PRESENTED_IMAGE_SIMPLE} description="暂无任务" />
      )}
    </Drawer>
  )
}
