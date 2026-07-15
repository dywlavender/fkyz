import { Button, Empty } from 'antd'

interface EmptyStateProps {
  title?: string
  actionLabel?: string
  onAction?: () => void
}

export function EmptyState({ title = '暂无数据', actionLabel, onAction }: EmptyStateProps) {
  return (
    <Empty
      description={title}
      image={Empty.PRESENTED_IMAGE_SIMPLE}
    >
      {actionLabel ? <Button onClick={onAction}>{actionLabel}</Button> : null}
    </Empty>
  )
}
