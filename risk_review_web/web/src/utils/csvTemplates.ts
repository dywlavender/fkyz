const sharedImportHeaders = [
  '案例名称',
  '因子名',
  '需求内容',
  '接口文档',
  '因子加工输入字段',
  '加工逻辑',
  '待评审内容',
  '默认值',
  '业务规则ID',
  '标准文件ID',
  '备注',
]

const sharedImportExample = [
  '近12个月医药采购金额 正向案例',
  '近12个月医药采购金额',
  '选取窗口内的采购金额并计算总和。',
  '授信申请接口、ES_医药企业药品采购信息查询接口',
  [
    '授信申请接口.applyTime',
    'ES_医药企业药品采购信息查询接口.content[].kpyf',
    'ES_医药企业药品采购信息查询接口.content[].inputAmount',
  ].join('\n'),
  [
    '1. 以 applyTime 作为统计终点，计算12个月窗口起点。',
    '2. 遍历 content[]，筛选窗口内的 kpyf 记录。',
    '3. 对保留记录的 inputAmount 求和。',
  ].join('\n'),
  '',
  '字段缺失、格式错误或数据异常时返回默认值',
  '',
  '',
  '模板示例，可直接替换为实际案例',
]

function csvCell(value: unknown) {
  const text = String(value ?? '')
  return `"${text.replace(/"/g, '""')}"`
}

export function downloadCsv(filename: string, rows: unknown[][]) {
  const content = `\ufeff${rows.map((row) => row.map(csvCell).join(',')).join('\r\n')}`
  const blob = new Blob([content], { type: 'text/csv;charset=utf-8' })
  const url = URL.createObjectURL(blob)
  const link = document.createElement('a')
  link.href = url
  link.download = filename
  document.body.appendChild(link)
  link.click()
  link.remove()
  URL.revokeObjectURL(url)
}

export function downloadBatchImportTemplate() {
  downloadCsv('risk-review-batch-template.csv', [sharedImportHeaders, sharedImportExample])
}

export function downloadScheduledTaskImportTemplate() {
  downloadCsv('risk-review-scheduled-task-template.csv', [sharedImportHeaders, sharedImportExample])
}
