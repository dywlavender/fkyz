import { ReloadOutlined } from '@ant-design/icons'
import { Button, Descriptions, Space, Spin, Tabs, Tag } from 'antd'
import { useCallback, useEffect, useState } from 'react'

import { fetchFactorProcessingSpec } from '../api/reviewApi'
import { HeaderActions } from '../components/AppLayout'
import { MarkdownViewer } from '../components/MarkdownViewer'
import type { AppNotice, FactorProcessingSpecResponse, FactorProcessingSpecView } from '../types/review'

function SpecContent({ data, fileName }: { data?: FactorProcessingSpecView; fileName: string }) {
  return (
    <Space direction="vertical" size={12} style={{ width: '100%' }}>
      <Descriptions className="spec-meta-strip" bordered size="small" column={3}>
        <Descriptions.Item label="规范名称">{data?.spec.title || '-'}</Descriptions.Item>
        <Descriptions.Item label="规范版本"><Tag color="blue">{data?.spec.spec_version || '-'}</Tag></Descriptions.Item>
        <Descriptions.Item label="规则数量">{data?.spec.rules?.length || 0}</Descriptions.Item>
      </Descriptions>
      <div className="work-surface document-surface">
        <MarkdownViewer content={data?.markdown || '# 开发规范\n\n暂无内容。'} fileName={fileName} />
      </div>
    </Space>
  )
}

export function DevelopmentSpec({ onNotice }: { onNotice: (notice: AppNotice) => void }) {
  const [data, setData] = useState<FactorProcessingSpecResponse>()
  const [loading, setLoading] = useState(false)
  const load = useCallback(async () => {
    setLoading(true)
    try {
      setData(await fetchFactorProcessingSpec())
    } catch (caught) {
      onNotice({ type: 'error', message: caught instanceof Error ? caught.message : '开发规范加载失败' })
    } finally {
      setLoading(false)
    }
  }, [onNotice])

  useEffect(() => { void load() }, [load])

  return (
    <Space className="app-page" direction="vertical" size={12} style={{ width: '100%' }}>
      <HeaderActions page="development-spec">
        <Button icon={<ReloadOutlined />} loading={loading} onClick={() => void load()}>刷新</Button>
      </HeaderActions>
      <Spin spinning={loading}>
        <Tabs
          defaultActiveKey="phase1"
          items={[
            {
              key: 'phase1',
              label: 'Phase 1 · 标准生成规范',
              children: <SpecContent data={data?.phase1} fileName="phase1_processing_spec.md" />,
            },
            {
              key: 'phase2',
              label: 'Phase 2 · 加工评审规范',
              children: <SpecContent data={data?.phase2} fileName="phase2_review_spec.md" />,
            },
          ]}
        />
      </Spin>
    </Space>
  )
}
