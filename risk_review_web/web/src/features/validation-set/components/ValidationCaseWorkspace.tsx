import { Button, Card, Empty, Input, Select, Space, Table, Tooltip, Typography } from 'antd'
import type { ColumnsType } from 'antd/es/table'
import type { Key } from 'react'

import type { ValidationCase } from '../../../types/review'
import { ALL_VALUE } from '../model'

interface ValidationCaseWorkspaceProps {
  query: string
  conclusionFilter: string
  matchFilter: string
  selectedRowKeys: Key[]
  filteredCases: ValidationCase[]
  totalCases: number
  sourceExampleCount: number
  loading: boolean
  regressionActive: boolean
  columns: ColumnsType<ValidationCase>
  currentPage: number
  pageSize: number
  onQueryChange: (value: string) => void
  onConclusionFilterChange: (value: string) => void
  onMatchFilterChange: (value: string) => void
  onSelectionChange: (keys: Key[]) => void
  onBatchRun: () => void
  onBatchDelete: () => void
  onOpenCase: (record: ValidationCase) => void
  onPageChange: (page: number, pageSize: number) => void
}

export function ValidationCaseWorkspace(props: ValidationCaseWorkspaceProps) {
  return (
    <>
      <Card className="compact-card" title="操作栏">
        <Space className="responsive-filters" wrap size={8} style={{ width: '100%' }}>
          <Input.Search className="responsive-filter-search responsive-filter-search-wide" allowClear placeholder="搜索案例名称 / 因子 / 标签 / 备注" value={props.query} onChange={(event) => props.onQueryChange(event.target.value)} />
          <Select className="responsive-filter-select" value={props.conclusionFilter} onChange={props.onConclusionFilterChange} options={[
            { label: '全部期望结论', value: ALL_VALUE },
            { label: '通过', value: 'pass' },
            { label: '不通过', value: 'fail' },
            { label: '无法评审', value: 'unreviewable' },
          ]} />
          <Select className="responsive-filter-select" value={props.matchFilter} onChange={props.onMatchFilterChange} options={[
            { label: '全部一致性', value: ALL_VALUE },
            { label: '一致', value: 'matched' },
            { label: '不一致', value: 'mismatched' },
            { label: '无法评审', value: 'unreviewable' },
            { label: '未执行', value: 'unrun' },
          ]} />
          <Tooltip title={props.selectedRowKeys.length ? `将执行已选 ${props.selectedRowKeys.length} 条案例` : '请先选择验证案例'}>
            <span><Button size="small" disabled={!props.selectedRowKeys.length || props.regressionActive} loading={props.regressionActive} onClick={props.onBatchRun}>批量执行</Button></span>
          </Tooltip>
          <Tooltip title={props.selectedRowKeys.length ? `将删除已选 ${props.selectedRowKeys.length} 条案例，删除会二次确认` : '请先选择验证案例'}>
            <span><Button size="small" danger disabled={!props.selectedRowKeys.length} onClick={props.onBatchDelete}>批量删除</Button></span>
          </Tooltip>
          <Typography.Text type="secondary">已选 {props.selectedRowKeys.length} 条，当前显示 {props.filteredCases.length} / {props.totalCases} 条</Typography.Text>
        </Space>
      </Card>

      <Card className="compact-card" title="验证案例列表">
        {props.filteredCases.length || props.loading ? (
          <Table
            rowKey="caseId"
            className="compact-table"
            loading={props.loading}
            columns={props.columns}
            dataSource={props.filteredCases}
            size="small"
            rowSelection={{
              selectedRowKeys: props.selectedRowKeys,
              onChange: props.onSelectionChange,
              renderCell: (_checked, _record, _index, originNode) => <span onClick={(event) => event.stopPropagation()}>{originNode}</span>,
            }}
            onRow={(record) => ({ onClick: () => props.onOpenCase(record) })}
            pagination={{
              current: props.currentPage,
              pageSize: props.pageSize,
              size: 'small',
              showSizeChanger: true,
              showTotal: (total) => `共 ${total} 条`,
              onChange: props.onPageChange,
            }}
            scroll={{ x: 1460 }}
          />
        ) : props.totalCases ? (
          <Empty description="没有符合筛选条件的验证案例" />
        ) : (
          <Empty description={props.sourceExampleCount ? '暂无验证案例，可点击新增案例创建。' : '暂无验证集数据'} />
        )}
      </Card>
    </>
  )
}
