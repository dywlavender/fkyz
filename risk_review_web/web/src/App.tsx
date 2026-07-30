import { App as AntdApp, ConfigProvider, FloatButton, Typography, message } from 'antd'
import { useCallback, useEffect, useMemo, useState, type ReactNode } from 'react'

import { AppLayout, PageActivity, type PageKey } from './components/AppLayout'
import { TaskCenterDrawer } from './components/TaskCenterDrawer'
import { ApiDocs } from './pages/ApiDocs'
import { BatchReview } from './pages/BatchReview'
import { History } from './pages/History'
import { ScheduledTasks } from './pages/ScheduledTasks'
import { Settings } from './pages/Settings'
import { SingleReview } from './pages/SingleReview'
import { QualityValidation } from './pages/QualityValidation'
import { ReviewModeExperimentPage } from './pages/ReviewModeExperiment'
import { LegacyWorkbench } from './pages/LegacyWorkbench'
import { DevelopmentSpec } from './pages/DevelopmentSpec'
import { RequirementReviewPage } from './features/requirement-review'
import { cancelTask, fetchApiDocs, fetchExamples, fetchGlobalPoolStatus, fetchHealth, fetchTasks } from './api/reviewApi'
import type { ApiDocItem, AppNotice, ExampleItem, GlobalPoolStatus, ReviewHistoryRecord, SystemHealth, TaskRecord } from './types/review'
import { ErrorState } from './components/ErrorState'
import { appTheme } from './styles/theme'

function detectMockMode(health?: SystemHealth | null) {
  return Boolean(health?.claude?.mock_mode)
}

function detectModelReady(health?: SystemHealth | null) {
  if (health?.claude?.mock_mode) return true
  if (health?.modelRuntime) return Boolean(health.modelRuntime.selectedAvailable)
  return Boolean(health?.claude?.claude_path)
}

function KeptPage({ active, children }: { active: boolean; children: ReactNode }) {
  return (
    <div style={{ display: active ? 'block' : 'none' }}>
      <PageActivity active={active}>{children}</PageActivity>
    </div>
  )
}

const PAGE_PATHS: Record<PageKey, string> = {
  'single-review': '/review/single',
  'requirement-review': '/requirement-review',
  'batch-review': '/review/batch',
  'scheduled-tasks': '/review/scheduled',
  'validation-set': '/quality/runs',
  'validation-cases': '/quality/cases',
  'review-mode-experiment': '/quality/experiment',
  history: '/records',
  'api-docs': '/system/apis',
  'development-spec': '/system/spec',
  settings: '/system/settings',
  legacy: '/system/legacy',
}

function pageFromLocation(): PageKey {
  const path = window.location.pathname.replace(/\/+$/, '') || '/'
  const exact = (Object.entries(PAGE_PATHS) as Array<[PageKey, string]>).find(([, value]) => value === path)
  if (exact) return exact[0]
  if (path.startsWith('/review/batch')) return 'batch-review'
  if (path.startsWith('/requirement-review')) return 'requirement-review'
  if (path.startsWith('/review/scheduled')) return 'scheduled-tasks'
  if (path.startsWith('/quality/experiment')) return 'review-mode-experiment'
  if (path.startsWith('/quality/cases')) return 'validation-cases'
  if (path.startsWith('/quality/')) return 'validation-set'
  if (path.startsWith('/records')) return 'history'
  if (path.startsWith('/system/settings')) return 'settings'
  if (path.startsWith('/system/spec')) return 'development-spec'
  if (path.startsWith('/system/legacy')) return 'legacy'
  if (path.startsWith('/system/')) return 'api-docs'
  return 'single-review'
}

export default function App() {
  const [page, setPage] = useState<PageKey>(() => pageFromLocation())
  const [visitedPages, setVisitedPages] = useState<Set<PageKey>>(
    () => new Set<PageKey>([pageFromLocation()])
  )
  const [apiDocs, setApiDocs] = useState<ApiDocItem[]>([])
  const [examples, setExamples] = useState<ExampleItem[]>([])
  const [health, setHealth] = useState<SystemHealth | null>(null)
  const [pool, setPool] = useState<GlobalPoolStatus | null>(null)
  const [tasks, setTasks] = useState<TaskRecord[]>([])
  const [taskCenterOpen, setTaskCenterOpen] = useState(false)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string>()
  const [focusedHistoryRecordId, setFocusedHistoryRecordId] = useState<string>()
  const [standardPrefill, setStandardPrefill] = useState<ReviewHistoryRecord | null>(null)
  const [messageApi, contextHolder] = message.useMessage()

  const mockMode = useMemo(() => detectMockMode(health), [health])
  const modelReady = useMemo(() => detectModelReady(health), [health])
  const activeTaskCount = tasks.filter((task) => ['queued', 'running', 'cancelling'].includes(String(task.status || ''))).length

  const navigate = useCallback((target: PageKey, replace = false) => {
    const path = PAGE_PATHS[target]
    if (window.location.pathname !== path) {
      window.history[replace ? 'replaceState' : 'pushState']({}, '', path)
    }
    setPage(target)
  }, [])

  useEffect(() => {
    async function bootstrap() {
      setLoading(true)
      setError(undefined)
      try {
        const [docs, systemHealth, exampleList] = await Promise.all([
          fetchApiDocs(),
          fetchHealth(),
          fetchExamples(),
        ])

        setApiDocs(docs)
        setHealth(systemHealth)
        setExamples(exampleList)
      } catch (caught) {
        const messageText = caught instanceof Error ? caught.message : '初始化失败'
        setError(messageText)
      } finally {
        setLoading(false)
      }
    }

    void bootstrap()
  }, [])

  useEffect(() => {
    let disposed = false
    async function refreshRuntime() {
      try {
        const [nextPool, nextTasks] = await Promise.all([
          fetchGlobalPoolStatus(),
          fetchTasks(),
        ])
        if (!disposed) {
          setPool(nextPool)
          setTasks(nextTasks)
        }
      } catch {
        if (!disposed) {
          setPool(null)
          setTasks([])
        }
      }
    }
    void refreshRuntime()
    const timer = window.setInterval(refreshRuntime, 2000)
    return () => {
      disposed = true
      window.clearInterval(timer)
    }
  }, [])

  useEffect(() => {
    const handlePopState = () => setPage(pageFromLocation())
    window.addEventListener('popstate', handlePopState)
    if (window.location.pathname === '/' || !Object.values(PAGE_PATHS).includes(window.location.pathname)) {
      navigate(pageFromLocation(), true)
    }
    return () => window.removeEventListener('popstate', handlePopState)
  }, [navigate])

  useEffect(() => {
    setVisitedPages((current) => {
      if (current.has(page)) return current
      const next = new Set(current)
      next.add(page)
      return next
    })
  }, [page])

  const handleNotice = useCallback((notice: AppNotice) => {
    messageApi.open({
      type: notice.type,
      content: notice.description ? `${notice.message}：${notice.description}` : notice.message,
    })
  }, [messageApi])

  function renderPage() {
    if (loading) {
      return <Typography.Paragraph>加载中...</Typography.Paragraph>
    }

    if (error) {
      return <ErrorState error={error} onRetry={() => window.location.reload()} />
    }

    const keep = (target: PageKey) => page === target || visitedPages.has(target)
    return (
      <>
        {keep('single-review') ? (
          <KeptPage active={page === 'single-review'}>
            <SingleReview
              apiDocs={apiDocs}
              examples={examples}
              onNotice={handleNotice}
              prefillStandardRecord={standardPrefill}
              onPrefillStandardConsumed={() => setStandardPrefill(null)}
            />
          </KeptPage>
        ) : null}

        {keep('batch-review') ? (
          <KeptPage active={page === 'batch-review'}>
            <BatchReview onNotice={handleNotice} />
          </KeptPage>
        ) : null}

        {keep('requirement-review') ? (
          <KeptPage active={page === 'requirement-review'}>
            <RequirementReviewPage apiDocs={apiDocs} onNotice={handleNotice} />
          </KeptPage>
        ) : null}

        {keep('scheduled-tasks') ? (
          <KeptPage active={page === 'scheduled-tasks'}>
            <ScheduledTasks onNotice={handleNotice} />
          </KeptPage>
        ) : null}

        {keep('validation-set') || keep('validation-cases') ? (
          <KeptPage active={page === 'validation-set' || page === 'validation-cases'}>
          <QualityValidation
            view={page === 'validation-cases' ? 'cases' : 'runs'}
            examples={examples}
            onNotice={handleNotice}
          />
          </KeptPage>
        ) : null}

        {keep('review-mode-experiment') ? (
          <KeptPage active={page === 'review-mode-experiment'}>
            <ReviewModeExperimentPage onNotice={handleNotice} />
          </KeptPage>
        ) : null}

        {keep('history') ? (
          <KeptPage active={page === 'history'}>
          <History
            selectedRecordId={focusedHistoryRecordId}
            onSelectedRecordConsumed={() => setFocusedHistoryRecordId(undefined)}
            onReuse={(record) => {
              setStandardPrefill(record)
              navigate('single-review')
            }}
            onNotice={handleNotice}
          />
          </KeptPage>
        ) : null}

        {keep('api-docs') ? (
          <KeptPage active={page === 'api-docs'}>
            <ApiDocs docs={apiDocs} onNotice={handleNotice} />
          </KeptPage>
        ) : null}

        {keep('settings') ? (
          <KeptPage active={page === 'settings'}>
            <Settings
              health={health}
              onNotice={handleNotice}
              onSettingsChanged={async () => setHealth(await fetchHealth())}
            />
          </KeptPage>
        ) : null}

        {keep('development-spec') ? (
          <KeptPage active={page === 'development-spec'}>
            <DevelopmentSpec onNotice={handleNotice} />
          </KeptPage>
        ) : null}

        {keep('legacy') ? (
          <KeptPage active={page === 'legacy'}>
            <LegacyWorkbench />
          </KeptPage>
        ) : null}
      </>
    )
  }

  return (
    <ConfigProvider theme={appTheme}>
      <AntdApp>
        {contextHolder}
        <AppLayout
          page={page}
          onNavigate={navigate}
          mockMode={mockMode}
          modelReady={modelReady}
          pool={pool}
          activeTaskCount={activeTaskCount}
          onOpenTaskCenter={() => setTaskCenterOpen(true)}
        >
          {renderPage()}
        </AppLayout>
        <TaskCenterDrawer
          open={taskCenterOpen}
          tasks={tasks}
          onClose={() => setTaskCenterOpen(false)}
          onCancel={(taskId) => {
            void cancelTask(taskId)
              .then(() => handleNotice({ type: 'warning', message: '已请求终止任务' }))
              .catch((caught) => handleNotice({ type: 'error', message: caught instanceof Error ? caught.message : '终止任务失败' }))
          }}
          onOpenTask={(task) => {
            setTaskCenterOpen(false)
            if (task.taskType === 'batch') navigate('batch-review')
            else if (task.taskType === 'requirement-review') navigate('requirement-review')
            else if (task.taskType === 'regression') navigate('validation-set')
            else if (task.taskType === 'review-mode-experiment') navigate('review-mode-experiment')
            else navigate('history')
          }}
        />
        <FloatButton.BackTop />
      </AntdApp>
    </ConfigProvider>
  )
}
