import { EyeOutlined } from '@ant-design/icons'
import { Alert, Button, Card, Descriptions, Drawer, Empty, Form, Input, InputNumber, Modal, Select, Space, Table, Typography } from 'antd'
import type { FormInstance } from 'antd'

import { MarkdownViewer } from '../../../components/MarkdownViewer'
import type { RegressionTaskItem, ValidationCase, ValidationCaseImportResult, ValidationRun } from '../../../types/review'
import { conclusionLabel, formatDate, runAlertType, runDescription, type RegressionDocumentType } from '../model'
import type { CaseFormValues } from '../types'

export function ValidationCaseDetailDrawer({
  active,
  validationCase,
  latestRun,
  onClose,
  onOpenRun,
}: {
  active: boolean
  validationCase: ValidationCase | null
  latestRun?: ValidationRun
  onClose: () => void
  onOpenRun: (run: ValidationRun) => void
}) {
  return (
    <Drawer className="drawer-compact" title={validationCase?.caseName || '案例详情'} width="min(820px, 90vw)" open={active && !!validationCase} mask={false} onClose={onClose}>
      {validationCase ? (
        <Space direction="vertical" size={12} style={{ width: '100%' }}>
          <Descriptions bordered column={1} size="small">
            <Descriptions.Item label="案例 ID">{validationCase.caseId}</Descriptions.Item>
            <Descriptions.Item label="因子名称">{validationCase.factorName}</Descriptions.Item>
            <Descriptions.Item label="期望结论">{conclusionLabel(validationCase.expectedConclusion)}</Descriptions.Item>
            <Descriptions.Item label="期望问题关键词">{validationCase.expectedIssuePatterns?.join('、') || '-'}</Descriptions.Item>
            <Descriptions.Item label="禁止问题关键词">{validationCase.forbiddenIssuePatterns?.join('、') || '-'}</Descriptions.Item>
            <Descriptions.Item label="最大问题数">{validationCase.maxIssueCount ?? '-'}</Descriptions.Item>
            <Descriptions.Item label="标签">{validationCase.tags?.length ? validationCase.tags.join('、') : '-'}</Descriptions.Item>
            <Descriptions.Item label="接口文档">{validationCase.apiDocIds?.join('、') || '-'}</Descriptions.Item>
            <Descriptions.Item label="业务规则">{validationCase.businessRuleIds?.join('、') || '-'}</Descriptions.Item>
            <Descriptions.Item label="备注">{validationCase.remark || '-'}</Descriptions.Item>
          </Descriptions>
          <Card className="compact-card" title="需求内容"><Typography.Paragraph style={{ whiteSpace: 'pre-wrap' }}>{validationCase.requirement}</Typography.Paragraph></Card>
          <Card className="compact-card" title="待评审内容"><Typography.Paragraph style={{ whiteSpace: 'pre-wrap' }}>{validationCase.reviewContent}</Typography.Paragraph></Card>
          <Card className="compact-card" title="最近执行结果">
            {latestRun ? (
              <Space direction="vertical" size={10} style={{ width: '100%' }}>
                <Alert showIcon type={runAlertType(latestRun)} message={`执行状态：${latestRun.status}`} description={runDescription(latestRun)} />
                <Descriptions bordered column={2} size="small">
                  <Descriptions.Item label="Run ID">{latestRun.runId}</Descriptions.Item>
                  <Descriptions.Item label="开始时间">{formatDate(latestRun.startedAt)}</Descriptions.Item>
                  <Descriptions.Item label="结束时间">{formatDate(latestRun.finishedAt)}</Descriptions.Item>
                  <Descriptions.Item label="标准文件">{latestRun.standardFile || '-'}</Descriptions.Item>
                  <Descriptions.Item label="报告文件">{latestRun.reviewFile || '-'}</Descriptions.Item>
                  <Descriptions.Item label="结论匹配">{latestRun.conclusionMatched == null ? '-' : latestRun.conclusionMatched ? '一致' : '不一致'}</Descriptions.Item>
                  <Descriptions.Item label="问题匹配">{latestRun.issuesMatched == null ? '-' : latestRun.issuesMatched ? '一致' : '不一致'}</Descriptions.Item>
                </Descriptions>
                <Button size="small" icon={<EyeOutlined />} onClick={() => onOpenRun(latestRun)}>打开运行详情</Button>
              </Space>
            ) : (
              <Empty image={Empty.PRESENTED_IMAGE_SIMPLE} description="该案例尚未执行验证" />
            )}
          </Card>
        </Space>
      ) : null}
    </Drawer>
  )
}

export function ValidationCaseEditorDrawer({
  active,
  editingCase,
  open,
  form,
  onClose,
  onSave,
}: {
  active: boolean
  editingCase: ValidationCase | null
  open: boolean
  form: FormInstance<CaseFormValues>
  onClose: () => void
  onSave: () => void
}) {
  return (
    <Drawer
      title={editingCase ? '编辑验证案例' : '新增验证案例'}
      className="drawer-compact"
      width="min(820px, 90vw)"
      open={active && open}
      mask={false}
      onClose={onClose}
      extra={<Space><Button onClick={onClose}>取消</Button><Button type="primary" onClick={onSave}>保存</Button></Space>}
    >
      <Form form={form} layout="vertical">
        <Form.Item name="caseName" label="案例名称" rules={[{ required: true, message: '请输入案例名称' }]}><Input /></Form.Item>
        <Form.Item name="factorName" label="因子名称" rules={[{ required: true, message: '请输入因子名称' }]}><Input /></Form.Item>
        <Form.Item name="expectedConclusion" label="期望结论">
          <Select options={[{ label: '通过', value: 'pass' }, { label: '不通过', value: 'fail' }, { label: '无法评审', value: 'unreviewable' }]} />
        </Form.Item>
        <Form.Item name="expectedIssuePatternsText" label="期望问题关键词"><Input.TextArea rows={2} placeholder="可选，每行一个；评审报告需全部包含" /></Form.Item>
        <Form.Item name="forbiddenIssuePatternsText" label="禁止问题关键词"><Input.TextArea rows={2} placeholder="可选，每行一个；评审报告不得出现" /></Form.Item>
        <Form.Item name="maxIssueCount" label="最大问题数"><InputNumber min={0} precision={0} placeholder="可选" style={{ width: '100%' }} /></Form.Item>
        <Form.Item name="apiDocIdsText" label="关联接口文档" rules={[{ required: true, message: '请输入至少一个接口文档' }]}><Input.TextArea rows={2} placeholder="每行一个，或用逗号分隔" /></Form.Item>
        <Form.Item name="businessRuleIdsText" label="业务规则 ID"><Input.TextArea rows={2} placeholder="可选，每行一个" /></Form.Item>
        <Form.Item name="requirement" label="需求内容" rules={[{ required: true, message: '请输入需求内容' }]}><Input.TextArea rows={4} /></Form.Item>
        <Form.Item name="reviewContent" label="待评审内容" rules={[{ required: true, message: '请输入待评审内容' }]}><Input.TextArea rows={6} /></Form.Item>
        <Form.Item name="tagsText" label="标签"><Input.TextArea rows={2} placeholder="可选，每行一个" /></Form.Item>
        <Form.Item name="remark" label="备注"><Input.TextArea rows={3} /></Form.Item>
      </Form>
    </Drawer>
  )
}

export function ValidationRunDrawer({
  active,
  open,
  run,
  documentType,
  fileId,
  content,
  loadingFile,
  onClose,
  onDocumentChange,
}: {
  active: boolean
  open: boolean
  run: ValidationRun | null
  documentType: RegressionDocumentType
  fileId?: string | null
  content?: string | null
  loadingFile?: string
  onClose: () => void
  onDocumentChange: (value: RegressionDocumentType) => void
}) {
  return (
    <Drawer className="drawer-compact" title="验证结果详情" width="min(860px, 90vw)" open={active && open} mask={false} onClose={onClose}>
      {run ? (
        <Space direction="vertical" size={12} style={{ width: '100%' }}>
          <Alert showIcon type={runAlertType(run)} message={`执行状态：${run.status}`} description={runDescription(run)} />
          <Descriptions bordered column={1} size="small">
            <Descriptions.Item label="Run ID">{run.runId}</Descriptions.Item>
            <Descriptions.Item label="案例 ID">{run.caseId}</Descriptions.Item>
            <Descriptions.Item label="开始时间">{formatDate(run.startedAt)}</Descriptions.Item>
            <Descriptions.Item label="结束时间">{formatDate(run.finishedAt)}</Descriptions.Item>
            <Descriptions.Item label="标准文件">{run.standardFile || '-'}</Descriptions.Item>
            <Descriptions.Item label="报告文件">{run.reviewFile || '-'}</Descriptions.Item>
          </Descriptions>
          <Space wrap>
            <Button type={documentType === 'review' ? 'primary' : 'default'} disabled={!run.reviewFile} onClick={() => onDocumentChange('review')}>查看评审结果</Button>
            <Button type={documentType === 'standard' ? 'primary' : 'default'} disabled={!run.standardFile} onClick={() => onDocumentChange('standard')}>查看标准</Button>
          </Space>
          <Card className="compact-card" title={documentType === 'review' ? '评审结果' : '标准'}>
            {loadingFile === fileId ? <Typography.Text type="secondary">正在读取文档...</Typography.Text> : (
              <MarkdownViewer content={content || run.error || '暂无文档内容'} fileName={fileId || (documentType === 'review' ? '评审结果.md' : '标准.md')} />
            )}
          </Card>
        </Space>
      ) : null}
    </Drawer>
  )
}

export function RegressionResultDrawer({
  active,
  item,
  documentType,
  fileId,
  content,
  loadingFile,
  onClose,
  onDocumentChange,
}: {
  active: boolean
  item: RegressionTaskItem | null
  documentType: RegressionDocumentType
  fileId?: string | null
  content?: string | null
  loadingFile?: string
  onClose: () => void
  onDocumentChange: (value: RegressionDocumentType) => void
}) {
  return (
    <Drawer className="drawer-compact" title={item?.title || item?.id || '回归结果详情'} width="min(900px, 92vw)" open={active && !!item} mask={false} onClose={onClose}>
      {item ? (
        <Space direction="vertical" size={12} style={{ width: '100%' }}>
          <Alert showIcon type={item.status === 'pass' ? 'success' : item.status === 'running' ? 'info' : 'error'} message={`执行状态：${item.status || '未知'}`} description={item.expectedOk ? '实际结论与期望一致' : item.status === 'running' ? '案例仍在执行' : '实际结论与期望不一致或执行失败'} />
          <Descriptions bordered column={1} size="small">
            <Descriptions.Item label="案例 ID">{item.id || '-'}</Descriptions.Item>
            <Descriptions.Item label="期望结论">{item.expected || '-'}</Descriptions.Item>
            <Descriptions.Item label="实际结论">{item.verdict ? conclusionLabel(item.verdict) : '-'}</Descriptions.Item>
            <Descriptions.Item label="标准文件">{item.standardFileId || '-'}</Descriptions.Item>
            <Descriptions.Item label="报告文件">{item.reviewFileId || '-'}</Descriptions.Item>
          </Descriptions>
          <Space wrap>
            <Button type={documentType === 'review' ? 'primary' : 'default'} disabled={!item.raw && !item.reportPreview && !item.reviewFileId} onClick={() => onDocumentChange('review')}>查看评审结果</Button>
            <Button type={documentType === 'standard' ? 'primary' : 'default'} disabled={!item.standardText && !item.standardFileId} onClick={() => onDocumentChange('standard')}>查看标准</Button>
          </Space>
          <Card className="compact-card" title={documentType === 'review' ? '评审结果' : '标准'}>
            {loadingFile === fileId ? <Typography.Text type="secondary">正在读取文档...</Typography.Text> : (
              <MarkdownViewer content={content || '暂无文档内容'} fileName={fileId || (documentType === 'review' ? '评审结果.md' : '标准.md')} />
            )}
          </Card>
        </Space>
      ) : null}
    </Drawer>
  )
}

export function ValidationImportResultModal({ active, result, onClose }: { active: boolean; result: ValidationCaseImportResult | null; onClose: () => void }) {
  return (
    <Modal title="案例导入结果" open={active && !!result} maskClosable={false} onCancel={onClose} footer={<Button type="primary" onClick={onClose}>关闭</Button>} width="min(900px, 90vw)">
      {result ? (
        <Space direction="vertical" size={12} style={{ width: '100%' }}>
          <Alert showIcon type={result.failed ? 'warning' : 'success'} message={`文件 ${result.fileName}：共 ${result.totalRows} 条，成功 ${result.success} 条，失败 ${result.failed} 条`} description="案例名称为空时自动使用因子名称；分列填写的因子加工输入字段、加工逻辑和默认值会自动合并为待评审内容。" />
          {result.errors.length ? (
            <Table rowKey={(record) => `${record.rowNumber}-${record.caseId || record.factorName || ''}`} className="compact-table" size="small" pagination={false} dataSource={result.errors} columns={[
              { title: '行号', dataIndex: 'rowNumber', width: 80 },
              { title: '案例名称', dataIndex: 'caseName', ellipsis: true, render: (value?: string) => value || '-' },
              { title: '因子名称', dataIndex: 'factorName', ellipsis: true, render: (value?: string) => value || '-' },
              { title: '失败原因', dataIndex: 'error', ellipsis: true },
            ]} />
          ) : <Typography.Text type="secondary">全部案例导入成功。</Typography.Text>}
        </Space>
      ) : null}
    </Modal>
  )
}
