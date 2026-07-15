import { Alert, Button, Space } from 'antd'

interface ErrorStateProps {
  title?: string
  error: string
  onRetry?: () => void
}

export function ErrorState({ title = '加载失败', error, onRetry }: ErrorStateProps) {
  return (
    <Alert
      type="error"
      showIcon
      message={title}
      description={
        <Space direction="vertical" size={8}>
          <span>{error}</span>
          {onRetry ? <Button onClick={onRetry}>重试</Button> : null}
        </Space>
      }
    />
  )
}
