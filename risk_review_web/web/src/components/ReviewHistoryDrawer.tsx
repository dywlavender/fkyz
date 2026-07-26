import { Button, Card, Descriptions, Drawer, Empty, Space, Spin, Table, Tabs, Tag, Typography } from 'antd'
import { useEffect, useState } from 'react'

import { fetchArtifactContent } from '../api/reviewApi'
import type { AppNotice, ArtifactGroup, ReviewHistoryRecord } from '../types/review'
import { formatDateTimeValue } from '../utils/reviewTransform'
import { MarkdownViewer, isMarkdownContent } from './MarkdownViewer'
import { StatusBadge } from './StatusBadge'

type ArtifactKind = 'md' | 'json'
type DocumentType = 'standard' | 'review'

const DOCUMENT_LABELS: Record<DocumentType, string> = {
  standard: '标准加工规范',
  review: '评审结果文档',
}

const KIND_LABELS: Record<ArtifactKind, string> = {
  md: 'Markdown',
  json: '元数据',
}

const SOURCE_LABELS: Record<string, string> = {
  single: '单因子',
  batch: '批量',
  scheduled: '定时',
  validation: '质量验证',
  experiment: '模式对比',
}

const MODE_LABELS: Record<string, string> = {
  phase1_only: '仅生成标准加工规范',
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
  return group?.md ? 'md' : group?.json ? 'json' : 'md'
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
  if (kind === 'md' || isMarkdownContent(content, filename)) {
    return <MarkdownViewer content={content || ''} fileName={filename} />
  }
  return <JsonBlock content={content || ''} />
}

function artifactKindItems(
  document: DocumentType,
  group: ArtifactGroup | undefined,
  contentCache: Record<string, string>,
  loadingFile?: string,
) {
  const kinds: ArtifactKind[] = document === 'standard' ? ['md', 'json'] : ['md']
  return kinds.map((kind) => {
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
    { label: '标准 Markdown', filename: record.standardArtifacts?.md },
    { label: '标准元数据', filename: record.standardArtifacts?.json },
    { label: '评审 MD', filename: record.reviewArtifacts?.md },
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
  const [selectedItemIndex, setSelectedItemIndex] = useState<number>()

  useEffect(() => {
    const firstItem = record?.items?.[0]
    setSelectedItemIndex(firstItem?.index)
    const itemStandard = firstItem?.standardFileId
      ? { md: firstItem.standardFileId, json: firstItem.standardMetadataFileId || undefined }
      : undefined
    const itemReview = firstItem?.reviewFileId ? { md: firstItem.reviewFileId } : undefined
    const document = itemStandard?.md || record?.standardArtifacts?.md ? 'standard' : 'review'
    const group = document === 'standard' ? itemStandard || record?.standardArtifacts : itemReview || record?.reviewArtifacts
    setActiveDocument(document)
    setActiveKind(defaultKind(group))
  }, [record?.recordId, record?.standardArtifacts, record?.reviewArtifacts])

  const selectedItem = record?.items?.find((item) => item.index === selectedItemIndex)
  const selectedStandardArtifacts = selectedItem?.standardFileId
    ? { md: selectedItem.standardFileId, json: selectedItem.standardMetadataFileId || undefined }
    : record?.standardArtifacts
  const selectedReviewArtifacts = selectedItem?.reviewFileId
    ? { md: selectedItem.reviewFileId }
    : record?.reviewArtifacts
  const activeGroup = activeDocument === 'standard' ? selectedStandardArtifacts : activeDocument === 'review' ? selectedReviewArtifacts : undefined
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
    const group = document === 'standard' ? selectedStandardArtifacts : selectedReviewArtifacts
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
              <span>评审结论</span>
              <StatusBadge value={record.conclusion || 'unreviewable'} />
            </div>
            <div className="detail-summary-item">
              <span>问题数量</span>
              <strong>{record.riskCount ?? 0}</strong>
            </div>
            <div className="detail-summary-item">
              <span>任务来源</span>
              <Tag>{sourceLabel(record.source)}</Tag>
            </div>
            <div className="detail-summary-item">
              <span>执行模式</span>
              <strong>{modeLabel(record.mode)}</strong>
            </div>
          </div>
          <Descriptions bordered size="small" column={2}>
            <Descriptions.Item label="因子名称">{record.factorName || '-'}</Descriptions.Item>
            <Descriptions.Item label="创建时间">{formatDateTimeValue(record.createdAt)}</Descriptions.Item>
            <Descriptions.Item label="来源">{sourceLabel(record.source)}</Descriptions.Item>
            <Descriptions.Item label="执行模式">{modeLabel(record.mode)}</Descriptions.Item>
            <Descriptions.Item label="最终结论"><StatusBadge value={record.conclusion || 'unreviewable'} /></Descriptions.Item>
            <Descriptions.Item label="风险数量">{record.riskCount ?? 0}</Descriptions.Item>
          </Descriptions>

          {record.items?.length ? (
            <Table
              rowKey={(item) => String(item.index)}
              size="small"
              pagination={record.items.length > 8 ? { pageSize: 8 } : false}
              dataSource={record.items}
              rowClassName={(item) => item.index === selectedItemIndex ? 'ant-table-row-selected' : ''}
              onRow={(item) => ({
                onClick: () => {
                  setSelectedItemIndex(item.index)
                  const nextDocument = item.standardFileId ? 'standard' : 'review'
                  setActiveDocument(nextDocument)
                  setActiveKind('md')
                },
              })}
              columns={[
                { title: '明细', render: (_value, item) => item.factorName || `第 ${(item.index ?? 0) + 1} 条` },
                { title: '状态', dataIndex: 'status', width: 110 },
                { title: '结论', dataIndex: 'conclusion', width: 110, render: (value) => value ? <StatusBadge value={value} /> : '-' },
                { title: '问题数', dataIndex: 'riskCount', width: 90, render: (value) => value ?? 0 },
              ]}
            />
          ) : null}

          <Tabs
            activeKey={activeDocument}
            onChange={(value) => changeDocument(value as DocumentType)}
            items={[
              {
                key: 'standard',
                label: (
                  <Space size={6}>
                    {DOCUMENT_LABELS.standard}
                    {!hasArtifacts(selectedStandardArtifacts) ? <Tag>缺失</Tag> : null}
                  </Space>
                ),
                children: (
                  <Card size="small" title={DOCUMENT_LABELS.standard}>
                    <Tabs
                      activeKey={activeKind}
                      onChange={(value) => setActiveKind(value as ArtifactKind)}
                      items={artifactKindItems('standard', selectedStandardArtifacts, contentCache, loadingFile)}
                    />
                  </Card>
                ),
              },
              {
                key: 'review',
                label: (
                  <Space size={6}>
                    {DOCUMENT_LABELS.review}
                    {!hasArtifacts(selectedReviewArtifacts) ? <Tag>缺失</Tag> : null}
                  </Space>
                ),
                children: (
                  <Card size="small" title={DOCUMENT_LABELS.review}>
                    <Tabs
                      activeKey={activeKind}
                      onChange={(value) => setActiveKind(value as ArtifactKind)}
                      items={artifactKindItems('review', selectedReviewArtifacts, contentCache, loadingFile)}
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
