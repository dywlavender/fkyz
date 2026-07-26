import { ReloadOutlined } from '@ant-design/icons'
import { Button, Descriptions, Space, Spin, Tag } from 'antd'
import { useCallback, useEffect, useState } from 'react'

import { fetchFactorProcessingSpec } from '../api/reviewApi'
import { HeaderActions } from '../components/AppLayout'
import { MarkdownViewer } from '../components/MarkdownViewer'
import type { AppNotice, FactorProcessingSpecResponse } from '../types/review'

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
      <Descriptions className="spec-meta-strip" bordered size="small" column={3}>
        <Descriptions.Item label="规范名称">{data?.spec.title || '-'}</Descriptions.Item>
        <Descriptions.Item label="规范版本"><Tag color="blue">{data?.spec.spec_version || '-'}</Tag></Descriptions.Item>
        <Descriptions.Item label="规则数量">{data?.spec.rules?.length || 0}</Descriptions.Item>
      </Descriptions>
      <Spin spinning={loading}>
        <div className="work-surface document-surface">
          <MarkdownViewer content={data?.markdown || '# 因子加工开发规范\n\n暂无内容。'} fileName="factor_processing_spec.md" />
        </div>
      </Spin>
    </Space>
  )
}
