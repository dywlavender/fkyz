import {
  DatabaseOutlined,
  FileSearchOutlined,
  HistoryOutlined,
  MenuFoldOutlined,
  MenuUnfoldOutlined,
  SettingOutlined,
} from '@ant-design/icons'
import { Badge, Button, Layout, Menu, Space, Tag, Typography } from 'antd'
import type { MenuProps } from 'antd'
import { createContext, useCallback, useContext, useEffect, useMemo, useState } from 'react'
import type { CSSProperties, ReactNode } from 'react'
import type { GlobalPoolStatus } from '../types/review'

export type PageKey =
  | 'single-review'
  | 'batch-review'
  | 'scheduled-tasks'
  | 'validation-set'
  | 'validation-cases'
  | 'review-mode-experiment'
  | 'history'
  | 'api-docs'
  | 'development-spec'
  | 'settings'
  | 'legacy'

interface AppLayoutProps {
  page: PageKey
  onNavigate: (page: PageKey) => void
  mockMode?: boolean
  claudeReady?: boolean
  headerActions?: ReactNode
  pool?: GlobalPoolStatus | null
  activeTaskCount?: number
  onOpenTaskCenter?: () => void
  children: ReactNode
}

const items: MenuProps['items'] = [
  {
    key: 'review-root',
    icon: <FileSearchOutlined />,
    label: '评审中心',
    children: [
      { key: 'single-review', label: '单因子评审' },
      { key: 'batch-review', label: '批量评审' },
      { key: 'scheduled-tasks', label: '定时任务' },
    ],
  },
  {
    key: 'quality-root',
    icon: <DatabaseOutlined />,
    label: '质量验证',
    children: [
      { key: 'validation-cases', label: '验证案例' },
      { key: 'validation-set', label: '验证历史记录' },
      { key: 'review-mode-experiment', label: '模式对比' },
    ],
  },
  { key: 'history', icon: <HistoryOutlined />, label: '评审记录' },
  {
    key: 'system-root',
    icon: <SettingOutlined />,
    label: '系统管理',
    children: [
      { key: 'api-docs', label: '接口文档' },
      { key: 'development-spec', label: '开发规范' },
      { key: 'settings', label: '运行设置' },
      { key: 'legacy', label: '原有功能台' },
    ],
  },
]

const pageMeta: Record<PageKey, { title: string; subtitle: string }> = {
  'single-review': { title: '评审中心', subtitle: '提交加工逻辑并获得确定的评审结论' },
  'batch-review': { title: '批量评审', subtitle: '批量导入、并发执行与结果回收' },
  'scheduled-tasks': { title: '定时评审', subtitle: '维护周期性任务与最近运行状态' },
  'validation-set': { title: '验证历史记录', subtitle: '查看验证批次、预期一致率与案例执行明细' },
  'validation-cases': { title: '验证案例', subtitle: '维护人工核验后的案例与期望结果' },
  'review-mode-experiment': { title: '模式对比', subtitle: '比较不同评审模式的结论与问题差异' },
  history: { title: '评审记录', subtitle: '统一追溯单因子、批量、定时和验证任务' },
  'api-docs': { title: '系统管理', subtitle: '接口资产、开发规范与运行状态' },
  'development-spec': { title: '因子加工开发规范', subtitle: '分别查看标准生成规则与加工评审规则' },
  settings: { title: '运行设置', subtitle: '统一配置并发上限，查看运行模式与环境状态' },
  legacy: { title: '原有功能台', subtitle: '保留尚未迁移的页面、按钮与调试入口' },
}

const PAGE_KEYS: PageKey[] = [
  'single-review', 'batch-review', 'scheduled-tasks',
  'validation-set', 'validation-cases', 'review-mode-experiment',
  'history', 'api-docs', 'development-spec', 'settings', 'legacy',
]

function parentMenuKey(page: PageKey): string | undefined {
  if (['single-review', 'batch-review', 'scheduled-tasks'].includes(page)) return 'review-root'
  if (['validation-set', 'validation-cases', 'review-mode-experiment'].includes(page)) return 'quality-root'
  if (['api-docs', 'development-spec', 'settings', 'legacy'].includes(page)) return 'system-root'
  return undefined
}

type HeaderActionsRegistry = (page: PageKey, actions: ReactNode | null) => void

const HeaderActionsContext = createContext<HeaderActionsRegistry>(() => {})
const PageActivityContext = createContext(true)

export function PageActivity({ active, children }: { active: boolean; children: ReactNode }) {
  return <PageActivityContext.Provider value={active}>{children}</PageActivityContext.Provider>
}

export function usePageActive() {
  return useContext(PageActivityContext)
}

export function HeaderActions({ page, children }: { page: PageKey; children: ReactNode }) {
  const registerHeaderActions = useContext(HeaderActionsContext)
  useEffect(() => {
    registerHeaderActions(page, children)
    return () => registerHeaderActions(page, null)
  }, [children, page, registerHeaderActions])
  return null
}

export function AppLayout(props: AppLayoutProps) {
  const {
    page,
    onNavigate,
    mockMode,
    claudeReady,
    pool,
    activeTaskCount = 0,
    onOpenTaskCenter,
    headerActions: propHeaderActions,
    children,
  } = props
  const [collapsed, setCollapsed] = useState(false)
  const [openKeys, setOpenKeys] = useState<string[]>(() => {
    const parent = parentMenuKey(page)
    return parent ? [parent] : []
  })
  const [slotHeaderActions, setSlotHeaderActions] = useState<Partial<Record<PageKey, ReactNode>>>({})
  const meta = pageMeta[page]
  const headerActions = propHeaderActions ?? slotHeaderActions[page]
  const shellStyle = useMemo(
    () => ({ '--sider-width': collapsed ? '72px' : '244px' }) as CSSProperties,
    [collapsed]
  )
  const registerHeaderActions = useCallback<HeaderActionsRegistry>((targetPage, actions) => {
    setSlotHeaderActions((current) => {
      if (current[targetPage] === actions) return current
      const next = { ...current }
      if (actions === null) delete next[targetPage]
      else next[targetPage] = actions
      return next
    })
  }, [])

  useEffect(() => {
    const parent = parentMenuKey(page)
    if (!parent || collapsed) return
    setOpenKeys((current) => current.includes(parent) ? current : [...current, parent])
  }, [collapsed, page])

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
          theme="light"
          selectedKeys={[page]}
          openKeys={collapsed ? [] : openKeys}
          onOpenChange={(keys) => setOpenKeys(keys)}
          items={items}
          onClick={(event: Parameters<NonNullable<MenuProps['onClick']>>[0]) => {
            if (PAGE_KEYS.includes(event.key as PageKey)) onNavigate(event.key as PageKey)
          }}
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
            <div className="sider-status-divider" />
            <div
              className="sider-status-row"
              title="所有登录位置共享当前 Python 进程内的全局执行池"
            >
              <span>全局池</span>
              <strong className="sider-status-value">
                {pool ? `${pool.activeWorkers}/${pool.maxWorkers}` : '--/--'}
              </strong>
            </div>
            <div className="sider-status-row">
              <span>排队</span>
              <Tag color={pool?.queuedJobs ? 'processing' : 'default'}>
                {pool?.queuedJobs ?? '--'}
              </Tag>
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
          <div className="app-header-actions">
            <Space size={8}>
              {headerActions}
              <Badge count={activeTaskCount} size="small" offset={[-2, 2]}>
                <Button onClick={onOpenTaskCenter}>任务中心</Button>
              </Badge>
            </Space>
          </div>
        </Layout.Header>
        <Layout.Content className="app-content">
          <HeaderActionsContext.Provider value={registerHeaderActions}>
            {children}
          </HeaderActionsContext.Provider>
        </Layout.Content>
      </Layout>
    </Layout>
  )
}
