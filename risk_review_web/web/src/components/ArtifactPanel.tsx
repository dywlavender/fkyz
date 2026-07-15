import { Button, Card, Descriptions, Empty, Space, Tabs, Typography } from 'antd'

interface ArtifactPanelProps {
  standardFileId?: string
  standardJsonFileId?: string
  standardRawFileId?: string
  standardValidationFileId?: string
  reviewFileId?: string
  reviewJsonFileId?: string
  reviewRawFileId?: string
  reviewValidationFileId?: string
  onOpen?: (fileId: string) => void
}

export function ArtifactPanel(props: ArtifactPanelProps) {
  const {
    standardFileId,
    standardJsonFileId,
    standardRawFileId,
    standardValidationFileId,
    reviewFileId,
    reviewJsonFileId,
    reviewRawFileId,
    reviewValidationFileId,
    onOpen,
  } = props

  const action = (fileId?: string) => fileId ? (
    <Space size={6}>
      <Button size="small" disabled={!fileId} onClick={() => fileId && onOpen?.(fileId)}>
        查看
      </Button>
      <Button size="small" disabled={!fileId} href={fileId ? `/outputs/${encodeURIComponent(fileId)}` : undefined} target="_blank">
        下载
      </Button>
    </Space>
  ) : <Empty image={Empty.PRESENTED_IMAGE_SIMPLE} description="暂无该产物" />

  const documentTable = (files: Array<{ label: string; file?: string }>) => (
    <Descriptions column={1} size="small" bordered>
      {files.map((item) => (
        <Descriptions.Item key={item.label} label={item.label}>
          <Space direction="vertical" size={6} style={{ width: '100%' }}>
            <Typography.Text copyable={!!item.file} type={item.file ? undefined : 'secondary'}>{item.file || '暂无该产物'}</Typography.Text>
            {action(item.file)}
          </Space>
        </Descriptions.Item>
      ))}
    </Descriptions>
  )

  return (
    <Card title="原始产物">
      <Tabs
        items={[
          {
            key: 'standard',
            label: '准出文档',
            children: documentTable([
              { label: 'Markdown', file: standardFileId },
              { label: 'JSON', file: standardJsonFileId },
              { label: 'Raw', file: standardRawFileId },
              { label: 'Validation', file: standardValidationFileId },
            ]),
          },
          {
            key: 'review',
            label: '评审结果文档',
            children: documentTable([
              { label: 'Markdown', file: reviewFileId },
              { label: 'JSON', file: reviewJsonFileId },
              { label: 'Raw', file: reviewRawFileId },
              { label: 'Validation', file: reviewValidationFileId },
            ]),
          },
        ]}
      />
    </Card>
  )
}
