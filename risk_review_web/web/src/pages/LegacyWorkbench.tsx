import { Alert, Button, Card, Space, Typography } from 'antd'

export function LegacyWorkbench() {
  return (
    <Space className="app-page legacy-workbench-page" direction="vertical" size={12} style={{ width: '100%' }}>
      <Alert
        type="info"
        showIcon
        message="原有功能完整保留"
        description="这里保留新页面暂未覆盖的业务知识规则、验证集编辑、调试按钮和其它原有入口。迁移完成前不会删除。"
        action={<Button href="/legacy/" target="_blank">新窗口打开</Button>}
      />
      <Card className="compact-card legacy-workbench-card" styles={{ body: { padding: 0 } }}>
        <iframe title="原有风控评审工作台" src="/legacy/" className="legacy-workbench-frame" />
      </Card>
      <Typography.Text type="secondary">
        原有工作台与新工作台调用同一个后端和全局任务池。
      </Typography.Text>
    </Space>
  )
}
