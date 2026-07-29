import { Alert, Button, Drawer, Spin, Typography } from 'antd'
import { useState } from 'react'

import { fetchOutputText } from '../../../api/reviewApi'
import { usePageActive } from '../../../components/AppLayout'
import { EmptyState } from '../../../components/EmptyState'
import { MarkdownViewer, isJsonContent, isMarkdownContent } from '../../../components/MarkdownViewer'
import type { AppNotice } from '../../../types/review'

export function ArtifactDocumentButton(props: {
  fileId?: string
  title: '标准加工规范' | '评审文档'
  onNotice: (notice: AppNotice) => void
}) {
  const pageActive = usePageActive()
  const { fileId, title, onNotice } = props
  const [open, setOpen] = useState(false)
  const [loading, setLoading] = useState(false)
  const [content, setContent] = useState('')
  const [error, setError] = useState<string>()
  const isJson = isJsonContent(content) || String(fileId || '').toLowerCase().endsWith('.json')
  const isMarkdown = !isJson && isMarkdownContent(content, fileId || `${title}.md`)

  async function openDocument() {
    setOpen(true)
    setError(undefined)
    if (!fileId) {
      setContent('')
      return
    }
    setLoading(true)
    try {
      setContent(await fetchOutputText(fileId))
    } catch (caught) {
      const messageText = caught instanceof Error ? caught.message : `读取${title}失败`
      setError(messageText)
      onNotice({ type: 'error', message: messageText })
    } finally {
      setLoading(false)
    }
  }

  return (
    <>
      <Button onClick={openDocument} disabled={!fileId}>查看{title}</Button>
      <Drawer title={title} open={pageActive && open} width="min(1080px, 82vw)" onClose={() => setOpen(false)}>
        <Spin spinning={loading}>
          {error ? (
            <Alert type="error" showIcon message={`${title}读取失败`} description={error} />
          ) : !fileId ? (
            <EmptyState title={`暂无${title}。`} />
          ) : isMarkdown ? (
            <MarkdownViewer content={content} fileName={fileId} />
          ) : isJson ? (
            <pre className="single-debug-log-pre"><code>{(() => {
              try {
                return JSON.stringify(JSON.parse(content), null, 2)
              } catch {
                return content || '暂无内容'
              }
            })()}</code></pre>
          ) : (
            <Typography.Paragraph code style={{ whiteSpace: 'pre-wrap', display: 'block' }}>{content || '暂无内容'}</Typography.Paragraph>
          )}
        </Spin>
      </Drawer>
    </>
  )
}
