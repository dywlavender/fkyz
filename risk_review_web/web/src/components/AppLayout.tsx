import {
  ApiOutlined,
  AppstoreOutlined,
  BarsOutlined,
  DatabaseOutlined,
  FileSearchOutlined,
  HistoryOutlined,
  MenuFoldOutlined,
  MenuUnfoldOutlined,
  ScheduleOutlined,
  SettingOutlined,
  ToolOutlined,
} from '@ant-design/icons'
import { Badge, Button, Layout, Menu, Space, Tag, Typography } from 'antd'
import type { MenuProps } from 'antd'
import { createContext, useContext, useEffect, useMemo, useState } from 'react'
import type { CSSProperties, ReactNode } from 'react'
import type { GlobalPoolStatus } from '../types/review'

export type PageKey =
  | 'workbench'
  | 'single-review'
  | 'batch-review'
  | 'scheduled-tasks'
  | 'validation-set'
  | 'history'
  | 'api-docs'
  | 'settings'
  | 'legacy'

interface AppLayoutProps {
  page: PageKey
  onNavigate: (page: PageKey) => void
  mockMode?: boolean
  claudeReady?: boolean
  headerActions?: ReactNode
  pool?: GlobalPoolStatus | null
  children: ReactNode
}

const navItems: Array<{ key: PageKey; icon: ReactNode; label: string }> = [
  { key: 'workbench', icon: <AppstoreOutlined />, label: '工作台' },
  { key: 'single-review', icon: <FileSearchOutlined />, label: '单因子评审' },
  { key: 'batch-review', icon: <BarsOutlined />, label: '批量评审' },
  { key: 'scheduled-tasks', icon: <ScheduleOutlined />, label: '定时任务' },
  { key: 'validation-set', icon: <DatabaseOutlined />, label: '验证集' },
  { key: 'history', icon: <HistoryOutlined />, label: '评审历史' },
  { key: 'api-docs', icon: <ApiOutlined />, label: '接口文档库' },
  { key: 'settings', icon: <SettingOutlined />, label: '系统设置' },
  { key: 'legacy', icon: <ToolOutlined />, label: '原有功能台' },
]

const items: MenuProps['items'] = [
  {
    key: 'group-execute',
    label: '评审执行',
    type: 'group',
    children: navItems.slice(0, 4),
  },
  {
    key: 'group-assets',
    label: '质量与资产',
    type: 'group',
    children: navItems.slice(4, 7),
  },
  {
    key: 'group-system',
    label: '系统',
    type: 'group',
    children: navItems.slice(7),
  },
]

const pageMeta: Record<PageKey, { title: string; subtitle: string }> = {
  workbench: { title: '工作台', subtitle: '评审队列、风险资产与操作入口总览' },
  'single-review': { title: '单因子评审', subtitle: 'Phase 1 标准生成与 Phase 2 准出评审' },
  'batch-review': { title: '批量评审', subtitle: '批量导入、并发执行与结果回收' },
  'scheduled-tasks': { title: '定时任务', subtitle: '周期任务、运行状态与执行记录' },
  'validation-set': { title: '验证集', subtitle: '回归案例、期望结论与质量验证' },
  history: { title: '评审历史', subtitle: '准出标准、评审报告与产物下载' },
  'api-docs': { title: '接口文档库', subtitle: '评审可引用的数据接口资产' },
  settings: { title: '系统设置', subtitle: '运行模式、依赖状态与环境配置' },
  legacy: { title: '原有功能台', subtitle: '保留尚未迁移或仍需使用的页面、按钮与调试入口' },
}

const HeaderActionsContext = createContext<(actions: ReactNode) => void>(() => {})

export function HeaderActions({ children }: { children: ReactNode }) {
  const setHeaderActions = useContext(HeaderActionsContext)
  useEffect(() => {
    setHeaderActions(children)
    return () => setHeaderActions(null)
  }, [children, setHeaderActions])
  return null
}

export function AppLayout(props: AppLayoutProps) {
  const { page, onNavigate, mockMode, claudeReady, pool, headerActions: propHeaderActions, children } = props
  const [collapsed, setCollapsed] = useState(false)
  const [slotHeaderActions, setSlotHeaderActions] = useState<ReactNode>(null)
  const meta = pageMeta[page]
  const headerActions = propHeaderActions ?? slotHeaderActions
  const shellStyle = useMemo(
    () => ({ '--sider-width': collapsed ? '72px' : '244px' }) as CSSProperties,
    [collapsed]
  )

  return (
    <Layout className={collapsed ? 'app-shell app-shell-collapsed' : 'app-shell'} style={shellStyle}>
      <Layout.Sider width={244} collapsedWidth={72} collapsed={collapsed} className="app-sider">
        <div className="brand-panel">
          <div className="brand-mark">AI</div>
          <div className="brand-copy">
            <Typography.Title level={5} className="brand-title">
              风控 AI 评审
            </Typography.Title>
            <Typography.Text className="brand-subtitle">
              Review Console
            </Typography.Text>
          </div>
        </div>
        <Menu
          className="app-nav"
          mode="inline"
          theme="dark"
          selectedKeys={[page]}
          items={items}
          onClick={(event: Parameters<NonNullable<MenuProps['onClick']>>[0]) => onNavigate(event.key as PageKey)}
        />
        <div className="sider-status" aria-label="系统运行状态">
          <Space direction="vertical" size={8} style={{ width: '100%' }}>
            <div className="sider-status-row">
              <span>运行模式</span>
              <Tag color={mockMode ? 'gold' : 'blue'}>{mockMode ? 'Mock' : 'Claude'}</Tag>
            </div>
            <div className="sider-status-row">
              <span>服务状态</span>
              <Badge status={claudeReady ? 'success' : 'warning'} text={claudeReady ? '就绪' : '待检查'} />
            </div>
          </Space>
        </div>
      </Layout.Sider>
      <Layout className="app-main">
        <Layout.Header className="app-header">
          <div className="app-header-main">
            <Button
              className="shell-toggle"
              type="text"
              icon={collapsed ? <MenuUnfoldOutlined /> : <MenuFoldOutlined />}
              onClick={() => setCollapsed((current) => !current)}
              aria-label={collapsed ? '展开侧边栏' : '收起侧边栏'}
            />
            <div className="app-header-copy">
              <Typography.Title level={4}>{meta.title}</Typography.Title>
              <Typography.Text>{meta.subtitle}</Typography.Text>
            </div>
          </div>
          {headerActions ? <div className="app-header-actions">{headerActions}</div> : null}
          <div className="global-pool-summary" title="所有登录位置共享当前 Python 进程内的全局执行池">
            <span>全局池</span>
            <strong>{pool ? `${pool.activeWorkers}/${pool.maxWorkers}` : '--/--'}</strong>
            <Tag color={pool?.queuedJobs ? 'processing' : 'default'}>排队 {pool?.queuedJobs ?? '--'}</Tag>
          </div>
        </Layout.Header>
        <Layout.Content className="app-content">
          <HeaderActionsContext.Provider value={setSlotHeaderActions}>
            {children}
          </HeaderActionsContext.Provider>
        </Layout.Content>
      </Layout>
    </Layout>
  )
}
