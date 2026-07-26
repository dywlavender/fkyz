import { Tag } from 'antd'

const STATUS_LABELS: Record<string, string> = {
  queued: '排队中',
  running: '执行中',
  cancelling: '停止中',
  cancelled: '已取消',
  done: '已完成',
  error: '执行失败',
}

const STATUS_COLORS: Record<string, string> = {
  queued: 'gold',
  running: 'processing',
  cancelling: 'orange',
  cancelled: 'default',
  done: 'success',
  error: 'error',
}

export function taskStatusLabel(value?: string) {
  return STATUS_LABELS[String(value || '').toLowerCase()] || '未知状态'
}

export function TaskStatusBadge({ value }: { value?: string }) {
  const normalized = String(value || '').toLowerCase()
  return <Tag color={STATUS_COLORS[normalized] || 'default'}>{taskStatusLabel(normalized)}</Tag>
}
