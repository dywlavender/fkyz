import { Button, Card, Descriptions, Drawer, Empty, Space, Spin, Tabs, Tag, Typography } from 'antd'
import { useEffect, useState } from 'react'

import { fetchArtifactContent } from '../api/reviewApi'
import type { AppNotice, ArtifactGroup, ReviewHistoryRecord } from '../types/review'
import { formatDateTimeValue } from '../utils/reviewTransform'
import { MarkdownViewer, isMarkdownContent } from './MarkdownViewer'
import { StatusBadge } from './StatusBadge'

type ArtifactKind = keyof ArtifactGroup
type DocumentType = 'standard' | 'review'

const DOCUMENT_LABELS: Record<DocumentType, string> = {
  standard: '准出文档',
  review: '评审结果文档',
}

const KIND_LABELS: Record<ArtifactKind, string> = {
  md: 'Markdown',
  json: 'JSON',
  raw: 'Raw',
  validation: 'Validation',
}

const SOURCE_LABELS: Record<string, string> = {
  single: '单因子',
  batch: '批量',
  scheduled: '定时',
  validation: '验证集',
}

const MODE_LABELS: Record<string, string> = {
  phase1_only: '仅生成准出标准',
  phase2_only: '仅评审结果',
  one_click: '一键评审',
  phase1_phase2: '一键评审',
}

interface ReviewHistoryDrawerProps {
  record?: ReviewHistoryRecord | null
  open: boolean
  onClose: () => void
  onNotice: (notice: AppNotice) => void
}

export function labelFromMap(map: Record<string, string>, value?: string) {
  if (!value) return '-'
  return map[value] || value
}

export function sourceLabel(value?: string) {
  return labelFromMap(SOURCE_LABELS, value)
}

export function modeLabel(value?: string) {
  return labelFromMap(MODE_LABELS, value)
}

export function hasArtifacts(group?: ArtifactGroup) {
  return Boolean(group && Object.values(group).some(Boolean))
}

function downloadHref(filename?: string) {
  return filename ? `/outputs/${encodeURIComponent(filename)}` : undefined
}

function formatJsonText(content: string) {
  try {
    return JSON.stringify(JSON.parse(content), null, 2)
  } catch {
    return content || '暂无内容'
  }
}

function defaultDocument(record?: ReviewHistoryRecord | null): DocumentType {
  if (!record) return 'standard'
  return hasArtifacts(record.standardArtifacts) ? 'standard' : 'review'
}

function defaultKind(group?: ArtifactGroup): ArtifactKind {
  return group?.md ? 'md' : group?.json ? 'json' : group?.raw ? 'raw' : group?.validation ? 'validation' : 'md'
}

function JsonBlock({ content }: { content: string }) {
  return (
    <pre
      style={{
        margin: 0,
        maxHeight: 'calc(100vh - 330px)',
        overflow: 'auto',
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
      <code>{formatJsonText(content)}</code>
    </pre>
  )
}

function RawBlock({ content }: { content: string }) {
  return (
    <pre
      style={{
        margin: 0,
        maxHeight: 'calc(100vh - 330px)',
        overflow: 'auto',
        padding: 16,
        borderRadius: 8,
        background: '#111827',
        color: '#f9fafb',
        whiteSpace: 'pre-wrap',
        wordBreak: 'break-word',
        fontSize: 13,
        lineHeight: 1.65,
      }}
    >
      {content || '暂无内容'}
    </pre>
  )
}

function MissingArtifact({ label }: { label: string }) {
  return <Empty image={Empty.PRESENTED_IMAGE_SIMPLE} description={`${label}不存在`} />
}

function ArtifactContent(props: {
  filename?: string
  kind: ArtifactKind
  content?: string
  loading: boolean
}) {
  const { filename, kind, content, loading } = props
  if (!filename) return <MissingArtifact label={KIND_LABELS[kind]} />
  if (loading) return <Spin />
  if (kind === 'md' || (kind === 'validation' && isMarkdownContent(content, filename))) {
    return <MarkdownViewer content={content || ''} fileName={filename} />
  }
  if (kind === 'raw') return <RawBlock content={content || ''} />
  return <JsonBlock content={content || ''} />
}

function artifactKindItems(group: ArtifactGroup | undefined, contentCache: Record<string, string>, loadingFile?: string) {
  return (Object.keys(KIND_LABELS) as ArtifactKind[]).map((kind) => {
    const filename = group?.[kind]
    return {
      key: kind,
      label: KIND_LABELS[kind],
      disabled: !filename,
      children: (
        <ArtifactContent
          filename={filename}
          kind={kind}
          content={filename ? contentCache[filename] : ''}
          loading={Boolean(filename && loadingFile === filename)}
        />
      ),
    }
  })
}

function DocumentDownloads({ record }: { record: ReviewHistoryRecord }) {
  const files = [
    { label: '准出 MD', filename: record.standardArtifacts?.md },
    { label: '准出 JSON', filename: record.standardArtifacts?.json },
    { label: '准出 Raw', filename: record.standardArtifacts?.raw },
    { label: '准出 Validation', filename: record.standardArtifacts?.validation },
    { label: '评审 MD', filename: record.reviewArtifacts?.md },
    { label: '评审 JSON', filename: record.reviewArtifacts?.json },
    { label: '评审 Raw', filename: record.reviewArtifacts?.raw },
    { label: '评审 Validation', filename: record.reviewArtifacts?.validation },
  ]

  return (
    <Space wrap>
      {files.map((file) => (
        <Button
          key={file.label}
          size="small"
          disabled={!file.filename}
          href={downloadHref(file.filename)}
          target="_blank"
          download={file.filename}
        >
          {file.label}
        </Button>
      ))}
    </Space>
  )
}

export function ReviewHistoryDrawer({ record, open, onClose, onNotice }: ReviewHistoryDrawerProps) {
  const [activeDocument, setActiveDocument] = useState<DocumentType>('standard')
  const [activeKind, setActiveKind] = useState<ArtifactKind>('md')
  const [contentCache, setContentCache] = useState<Record<string, string>>({})
  const [loadingFile, setLoadingFile] = useState<string>()

  useEffect(() => {
    const document = defaultDocument(record)
    const group = document === 'standard' ? record?.standardArtifacts : record?.reviewArtifacts
    setActiveDocument(document)
    setActiveKind(defaultKind(group))
  }, [record?.recordId, record?.standardArtifacts, record?.reviewArtifacts])

  const activeGroup = activeDocument === 'standard' ? record?.standardArtifacts : activeDocument === 'review' ? record?.reviewArtifacts : undefined
  const activeFilename = activeGroup?.[activeKind]

  useEffect(() => {
    if (!activeFilename || contentCache[activeFilename]) return
    setLoadingFile(activeFilename)
    fetchArtifactContent(activeFilename)
      .then((content) => setContentCache((current) => ({ ...current, [activeFilename]: content })))
      .catch((caught) => {
        const message = caught instanceof Error ? caught.message : '读取产物失败'
        onNotice({ type: 'error', message })
      })
      .finally(() => setLoadingFile((current) => (current === activeFilename ? undefined : current)))
  }, [activeFilename, contentCache, onNotice])

  function changeDocument(document: DocumentType) {
    if (!record) return
    const group = document === 'standard' ? record.standardArtifacts : record.reviewArtifacts
    setActiveDocument(document)
    setActiveKind(defaultKind(group))
  }

  return (
    <Drawer
      className="drawer-compact"
      title={record?.factorName || '评审历史详情'}
      open={open}
      width="min(920px, 90vw)"
      onClose={onClose}
      extra={record ? <DocumentDownloads record={record} /> : null}
    >
      {record ? (
        <Space direction="vertical" size={12} style={{ width: '100%' }}>
          <div className="detail-summary-strip">
            <div className="detail-summary-item">
              <span>Conclusion</span>
              <StatusBadge value={record.conclusion || 'confirm'} />
            </div>
            <div className="detail-summary-item">
              <span>Risk Count</span>
              <strong>{record.riskCount ?? 0}</strong>
            </div>
            <div className="detail-summary-item">
              <span>Source</span>
              <Tag>{sourceLabel(record.source)}</Tag>
            </div>
            <div className="detail-summary-item">
              <span>Mode</span>
              <strong>{modeLabel(record.mode)}</strong>
            </div>
          </div>
          <Descriptions bordered size="small" column={2}>
            <Descriptions.Item label="因子名称">{record.factorName || '-'}</Descriptions.Item>
            <Descriptions.Item label="创建时间">{formatDateTimeValue(record.createdAt)}</Descriptions.Item>
            <Descriptions.Item label="来源">{sourceLabel(record.source)}</Descriptions.Item>
            <Descriptions.Item label="执行模式">{modeLabel(record.mode)}</Descriptions.Item>
            <Descriptions.Item label="最终结论"><StatusBadge value={record.conclusion || 'confirm'} /></Descriptions.Item>
            <Descriptions.Item label="风险数量">{record.riskCount ?? 0}</Descriptions.Item>
          </Descriptions>

          <Tabs
            activeKey={activeDocument}
            onChange={(value) => changeDocument(value as DocumentType)}
            items={[
              {
                key: 'standard',
                label: (
                  <Space size={6}>
                    {DOCUMENT_LABELS.standard}
                    {!hasArtifacts(record.standardArtifacts) ? <Tag>缺失</Tag> : null}
                  </Space>
                ),
                children: (
                  <Card size="small" title={DOCUMENT_LABELS.standard}>
                    <Tabs
                      activeKey={activeKind}
                      onChange={(value) => setActiveKind(value as ArtifactKind)}
                      items={artifactKindItems(record.standardArtifacts, contentCache, loadingFile)}
                    />
                  </Card>
                ),
              },
              {
                key: 'review',
                label: (
                  <Space size={6}>
                    {DOCUMENT_LABELS.review}
                    {!hasArtifacts(record.reviewArtifacts) ? <Tag>缺失</Tag> : null}
                  </Space>
                ),
                children: (
                  <Card size="small" title={DOCUMENT_LABELS.review}>
                    <Tabs
                      activeKey={activeKind}
                      onChange={(value) => setActiveKind(value as ArtifactKind)}
                      items={artifactKindItems(record.reviewArtifacts, contentCache, loadingFile)}
                    />
                  </Card>
                ),
              },
            ]}
          />
        </Space>
      ) : (
        <Typography.Text type="secondary">暂无记录。</Typography.Text>
      )}
    </Drawer>
  )
}
