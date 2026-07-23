import { Button, Card, Col, Empty, Input, List, Row, Space, Statistic, Tag, Tooltip, Typography } from 'antd'
import { useEffect, useMemo, useState, type ChangeEvent } from 'react'

import { fetchApiDocDetail } from '../api/reviewApi'
import { HeaderActions } from '../components/AppLayout'
import type { ApiDocItem, AppNotice } from '../types/review'
import { ErrorState } from '../components/ErrorState'
import { MarkdownViewer, isJsonContent } from '../components/MarkdownViewer'

interface ApiDocsProps {
  docs: ApiDocItem[]
  onNotice: (notice: AppNotice) => void
}

function formatJsonContent(content: string) {
  try {
    return JSON.stringify(JSON.parse(content), null, 2)
  } catch {
    return content
  }
}

function renderDocContent(doc: ApiDocItem) {
  const content = doc.content || ''
  if (isJsonContent(content)) {
    return (
      <pre
        className="json-viewer api-doc-detail-content"
        style={{
          margin: 0,
          padding: 16,
          borderRadius: 8,
          background: '#0f172a',
          color: '#e5e7eb',
          whiteSpace: 'pre-wrap',
          wordBreak: 'break-word',
          fontSize: 13,
          lineHeight: 1.65,
        }}
      >
        <code>{formatJsonContent(content)}</code>
      </pre>
    )
  }
  return (
    <div className="api-doc-detail-content">
      <MarkdownViewer content={content || '暂无文档内容。'} fileName={doc.path || doc.name} />
    </div>
  )
}

export function ApiDocs({ docs, onNotice }: ApiDocsProps) {
  const [query, setQuery] = useState('')
  const [active, setActive] = useState<ApiDocItem | null>(null)
  const [detail, setDetail] = useState<ApiDocItem | null>(null)
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState<string>()

  const filtered = useMemo(() => {
    const lower = query.trim().toLowerCase()
    if (!lower) return docs
    return docs.filter((item) =>
      [item.name, item.path, item.method, item.category, ...(item.keywords || [])]
        .filter(Boolean)
        .some((value) => String(value).toLowerCase().includes(lower))
    )
  }, [docs, query])

  useEffect(() => {
    if (!filtered.length) {
      setActive(null)
      setDetail(null)
      return
    }
    if (!active || !filtered.some((item) => item.name === active.name)) {
      setActive(filtered[0])
    }
  }, [filtered, active])

  useEffect(() => {
    async function load() {
      if (!active) return
      setLoading(true)
      setError(undefined)
      try {
        const next = await fetchApiDocDetail(active.name)
        setDetail(next)
      } catch (caught) {
        const message = caught instanceof Error ? caught.message : '加载接口文档失败'
        setError(message)
        onNotice({ type: 'error', message })
      } finally {
        setLoading(false)
      }
    }
    void load()
  }, [active, onNotice])

  async function reloadActiveDoc() {
    if (!active) return
    setLoading(true)
    setError(undefined)
    try {
      const next = await fetchApiDocDetail(active.name)
      setDetail(next)
      onNotice({ type: 'success', message: '接口文档已刷新' })
    } catch (caught) {
      const message = caught instanceof Error ? caught.message : '刷新接口文档失败'
      setError(message)
      onNotice({ type: 'error', message })
    } finally {
      setLoading(false)
    }
  }

  return (
    <Space className="app-page" direction="vertical" size={12} style={{ width: '100%' }}>
      <HeaderActions page="api-docs">
        <Space className="app-header-action-space" size={8} wrap>
          <Button loading={loading} disabled={!active} onClick={reloadActiveDoc}>刷新当前文档</Button>
          <Tooltip title="后端暂未提供接口文档上传 API">
            <span><Button disabled>上传接口文档</Button></span>
          </Tooltip>
        </Space>
      </HeaderActions>

      <Row gutter={[12, 12]}>
        <Col xs={12} md={6}><Card className="compact-card"><Statistic title="文档总数" value={docs.length} /></Card></Col>
        <Col xs={12} md={6}><Card className="compact-card"><Statistic title="当前筛选" value={filtered.length} /></Card></Col>
        <Col xs={12} md={6}><Card className="compact-card"><Statistic title="已加载详情" value={detail ? 1 : 0} /></Card></Col>
        <Col xs={12} md={6}><Card className="compact-card"><Statistic title="JSON 文档" value={docs.filter((item) => item.name?.toLowerCase().endsWith('.json') || item.path?.toLowerCase().endsWith('.json')).length} /></Card></Col>
      </Row>

      <div className="api-docs-layout">
        <Card className="compact-card api-doc-list-card" title="文档列表">
          <Space direction="vertical" size={8} style={{ width: '100%' }}>
            <Input.Search placeholder="搜索接口名或字段" value={query} onChange={(event: ChangeEvent<HTMLInputElement>) => setQuery(event.target.value)} />
            {filtered.length ? (
              <List
                loading={loading}
                dataSource={filtered}
                renderItem={(item: ApiDocItem) => (
                  <List.Item
                    style={{
                      cursor: 'pointer',
                      background: active?.name === item.name ? '#f4f8ff' : 'transparent',
                      borderRadius: 6,
                      padding: '8px 10px',
                    }}
                    onClick={() => setActive(item)}
                  >
                    <List.Item.Meta
                      title={item.name}
                      description={
                        <Space wrap>
                          {item.method ? <Tag color="blue">{item.method.toUpperCase()}</Tag> : null}
                          {item.category ? <Tag>{item.category}</Tag> : null}
                          {item.version ? <Tag>v{item.version}</Tag> : null}
                        </Space>
                      }
                    />
                  </List.Item>
                )}
              />
            ) : (
              <Empty description="无匹配接口文档" />
            )}
          </Space>
        </Card>

        <Card title="文档详情" className="api-doc-detail compact-card">
          {error ? (
            <ErrorState error={error} />
          ) : detail ? (
            <Space direction="vertical" size={10} style={{ width: '100%', minHeight: 0 }}>
              <Space wrap>
                {detail.method ? <Tag color="blue">{detail.method.toUpperCase()}</Tag> : null}
                {detail.version ? <Tag>v{detail.version}</Tag> : null}
                {detail.status ? <Tag>{detail.status}</Tag> : null}
                {detail.provider ? <Tag>{detail.provider}</Tag> : null}
              </Space>
              <Typography.Title level={5} style={{ margin: 0 }}>{detail.name}</Typography.Title>
              <Typography.Text type="secondary" style={{ fontSize: 12 }}>{detail.url || detail.path}</Typography.Text>
              {renderDocContent(detail)}
            </Space>
          ) : (
            <Empty description="选择一个接口文档查看详情" />
          )}
        </Card>
      </div>
    </Space>
  )
}
