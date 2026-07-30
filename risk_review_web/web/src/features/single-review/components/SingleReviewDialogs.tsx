import { Drawer, Empty, Input, Modal, Select, Space, Spin, Table, Tabs, Tag, Timeline, Typography } from 'antd'
import type { ColumnsType } from 'antd/es/table'

import { EmptyState } from '../../../components/EmptyState'
import { MarkdownViewer, isJsonContent, isMarkdownContent } from '../../../components/MarkdownViewer'
import type { ApiDocItem } from '../../../types/review'
import type { CaseOption, ExecutionLogItem } from '../types'

function ExecutionTimeline({ items }: { items: ExecutionLogItem[] }) {
  const logTagColor: Record<ExecutionLogItem['level'], string> = { info: 'blue', success: 'green', warning: 'orange', error: 'red' }
  const logPhaseText: Record<ExecutionLogItem['phase'], string> = { phase1: '生成标准', phase2: '执行评审', system: '系统' }
  if (!items.length) return <EmptyState title="暂无执行日志。" />
  return (
    <Timeline items={items.map((item) => ({
      color: item.level === 'error' ? 'red' : item.level === 'warning' ? 'orange' : item.level === 'success' ? 'green' : 'blue',
      children: (
        <Space direction="vertical" size={4} style={{ width: '100%' }}>
          <Space size={6} wrap>
            <Typography.Text type="secondary">{item.time}</Typography.Text>
            <Tag color={logTagColor[item.level]}>{logPhaseText[item.phase]}</Tag>
            {item.stream ? <Tag>{item.stream}</Tag> : null}
          </Space>
          <Typography.Text>{item.message}</Typography.Text>
          {item.detail ? <pre className={item.isDebug ? 'single-debug-log-pre' : 'single-log-detail-pre'}>{item.detail}</pre> : null}
        </Space>
      ),
    }))} />
  )
}

export function RawArtifactModal({ active, open, title, content, onClose }: { active: boolean; open: boolean; title: string; content: string; onClose: () => void }) {
  const isJson = isJsonContent(content) || title.toLowerCase().endsWith('.json')
  const isMarkdown = !isJson && isMarkdownContent(content, title)
  return (
    <Modal title={title} open={active && open} maskClosable={false} width="min(860px, 90vw)" footer={null} onCancel={onClose}>
      {isMarkdown ? <MarkdownViewer content={content} fileName={title} /> : isJson ? (
        <pre style={{ margin: 0, maxHeight: 'calc(100vh - 220px)', overflow: 'auto', padding: 16, borderRadius: 8, background: '#0f172a', color: '#e5e7eb', whiteSpace: 'pre-wrap', wordBreak: 'break-word', fontSize: 13, lineHeight: 1.65 }}>
          <code>{(() => {
            try {
              return JSON.stringify(JSON.parse(content), null, 2)
            } catch {
              return content || '暂无内容'
            }
          })()}</code>
        </pre>
      ) : <Typography.Paragraph code style={{ whiteSpace: 'pre-wrap', display: 'block' }}>{content || '暂无内容'}</Typography.Paragraph>}
    </Modal>
  )
}

export function ApiDocumentDrawer({ active, open, loading, detail, selectedNames, onClose, onSelect }: { active: boolean; open: boolean; loading: boolean; detail: ApiDocItem | null; selectedNames: string[]; onClose: () => void; onSelect: (name: string) => void }) {
  return (
    <Drawer title="接口文档" width="min(920px, 78vw)" open={active && open} onClose={onClose} extra={(
      <Select style={{ width: 280 }} value={detail?.name} placeholder="切换已选接口" options={selectedNames.map((name) => ({ label: name, value: name }))} onChange={onSelect} />
    )}>
      <Spin spinning={loading}>
        {detail?.content ? <MarkdownViewer content={detail.content} fileName={`${detail.name}.md`} /> : <EmptyState title="暂无接口文档内容。" />}
      </Spin>
    </Drawer>
  )
}

export function ExecutionLogModal({ active, open, logs, onClose }: { active: boolean; open: boolean; logs: ExecutionLogItem[]; onClose: () => void }) {
  const timelineLogs = [...logs].reverse()
  const runtimeLogs = timelineLogs.filter((item) => !item.isDebug)
  const debugLogs = timelineLogs.filter((item) => item.isDebug)
  return (
    <Modal title="执行日志" open={active && open} maskClosable={false} width="min(960px, 92vw)" footer={null} onCancel={onClose}>
      <Tabs size="small" items={[
        { key: 'runtime', label: `运行日志（${runtimeLogs.length}）`, children: <div className="execution-log-modal-body"><ExecutionTimeline items={runtimeLogs} /></div> },
        { key: 'debug', label: `模型输入/输出/工具调用（${debugLogs.length}）`, children: <div className="execution-log-modal-body"><ExecutionTimeline items={debugLogs} /></div> },
      ]} />
    </Modal>
  )
}

export function CaseSelectionModal({
  active,
  open,
  loading,
  executionDisabled,
  query,
  options,
  columns,
  selectedKey,
  rowKey,
  onQueryChange,
  onSelect,
  onClose,
  onConfirm,
}: {
  active: boolean
  open: boolean
  loading: boolean
  executionDisabled: boolean
  query: string
  options: CaseOption[]
  columns: ColumnsType<CaseOption>
  selectedKey?: string
  rowKey: (record: CaseOption) => string
  onQueryChange: (value: string) => void
  onSelect: (key: string) => void
  onClose: () => void
  onConfirm: () => void
}) {
  return (
    <Modal title="加载验证案例" open={active && open} maskClosable={false} width="min(920px, 90vw)" onCancel={onClose} onOk={onConfirm} okText="确认加载" okButtonProps={{ disabled: !selectedKey || executionDisabled }}>
      <Space direction="vertical" size={12} style={{ width: '100%' }}>
        <Input.Search allowClear disabled={executionDisabled} placeholder="按案例名称 / 因子名称搜索" value={query} onChange={(event) => onQueryChange(event.target.value)} />
        <Table
          rowKey={rowKey}
          className="compact-table"
          size="small"
          loading={loading}
          dataSource={options}
          columns={columns}
          pagination={{ pageSize: 6, size: 'small' }}
          rowSelection={{ type: 'radio', selectedRowKeys: selectedKey ? [selectedKey] : [], getCheckboxProps: () => ({ disabled: executionDisabled }), onChange: (keys) => { if (!executionDisabled) onSelect(String(keys[0] || '')) } }}
          onRow={(record) => ({ onClick: () => { if (!executionDisabled) onSelect(rowKey(record)) } })}
          scroll={{ x: 820 }}
        />
        {!selectedKey ? <Empty image={Empty.PRESENTED_IMAGE_SIMPLE} description="请选择一个验证案例。" /> : null}
      </Space>
    </Modal>
  )
}

export function ClearPageConfirmModal({ active, open, executionDisabled, onClose, onConfirm }: { active: boolean; open: boolean; executionDisabled: boolean; onClose: () => void; onConfirm: () => void }) {
  return (
    <Modal title="确认清空当前页面内容？" open={active && open} maskClosable={false} onCancel={onClose} onOk={onConfirm} okText="清空" cancelText="取消" okButtonProps={{ danger: true, disabled: executionDisabled }}>
      <Typography.Paragraph>清空会重置输入、评审结果和执行状态，不会删除历史产物。</Typography.Paragraph>
    </Modal>
  )
}
