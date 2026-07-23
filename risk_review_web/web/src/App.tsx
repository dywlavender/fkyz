import { FileSearchOutlined, HistoryOutlined } from '@ant-design/icons'
import { App as AntdApp, Button, ConfigProvider, FloatButton, Space, Typography, message } from 'antd'
import { useCallback, useEffect, useMemo, useState, type ReactNode } from 'react'

import { AppLayout, PageActivity, type PageKey } from './components/AppLayout'
import { ApiDocs } from './pages/ApiDocs'
import { BatchReview } from './pages/BatchReview'
import { History } from './pages/History'
import { ScheduledTasks } from './pages/ScheduledTasks'
import { Settings } from './pages/Settings'
import { SingleReview } from './pages/SingleReview'
import { ValidationSet } from './pages/ValidationSet'
import { Workbench } from './pages/Workbench'
import { LegacyWorkbench } from './pages/LegacyWorkbench'
import { fetchApiDocs, fetchExamples, fetchGlobalPoolStatus, fetchHealth, fetchReviewHistory } from './api/reviewApi'
import type { ApiDocItem, AppNotice, ExampleItem, GlobalPoolStatus, ReviewHistoryRecord, SystemHealth, ValidationCase } from './types/review'
import { ErrorState } from './components/ErrorState'
import { appTheme } from './styles/theme'

function detectMockMode(health?: SystemHealth | null) {
  return Boolean(health?.claude?.mock_mode)
}

function detectClaudeReady(health?: SystemHealth | null) {
  return Boolean(health?.claude?.claude_path) || Boolean(health?.claude?.mock_mode)
}

function KeptPage({ active, children }: { active: boolean; children: ReactNode }) {
  return (
    <div style={{ display: active ? 'block' : 'none' }}>
      <PageActivity active={active}>{children}</PageActivity>
    </div>
  )
}

export default function App() {
  const [page, setPage] = useState<PageKey>('workbench')
  const [visitedPages, setVisitedPages] = useState<Set<PageKey>>(
    () => new Set<PageKey>(['workbench', 'single-review'])
  )
  const [apiDocs, setApiDocs] = useState<ApiDocItem[]>([])
  const [examples, setExamples] = useState<ExampleItem[]>([])
  const [history, setHistory] = useState<ReviewHistoryRecord[]>([])
  const [health, setHealth] = useState<SystemHealth | null>(null)
  const [pool, setPool] = useState<GlobalPoolStatus | null>(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string>()
  const [focusedHistoryRecordId, setFocusedHistoryRecordId] = useState<string>()
  const [singlePrefill, setSinglePrefill] = useState<ValidationCase | null>(null)
  const [standardPrefill, setStandardPrefill] = useState<ReviewHistoryRecord | null>(null)
  const [messageApi, contextHolder] = message.useMessage()

  const mockMode = useMemo(() => detectMockMode(health), [health])
  const claudeReady = useMemo(() => detectClaudeReady(health), [health])
  const headerActions = page === 'workbench' ? (
    <Space className="app-header-action-space" size={8} wrap>
      <Button type="primary" icon={<FileSearchOutlined />} onClick={() => setPage('single-review')}>新建单因子评审</Button>
      <Button icon={<HistoryOutlined />} onClick={() => setPage('history')}>查看评审历史</Button>
    </Space>
  ) : null

  useEffect(() => {
    async function bootstrap() {
      setLoading(true)
      setError(undefined)
      try {
        const [docs, outputList, systemHealth, exampleList] = await Promise.all([
          fetchApiDocs(),
          fetchReviewHistory(),
          fetchHealth(),
          fetchExamples(),
        ])

        setApiDocs(docs)
        setHistory(outputList)
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
    async function refreshPool() {
      try {
        const next = await fetchGlobalPoolStatus()
        if (!disposed) setPool(next)
      } catch {
        if (!disposed) setPool(null)
      }
    }
    void refreshPool()
    const timer = window.setInterval(refreshPool, 2000)
    return () => {
      disposed = true
      window.clearInterval(timer)
    }
  }, [])

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
        {keep('workbench') ? (
          <KeptPage active={page === 'workbench'}>
          <Workbench
            history={history}
            onOpenHistory={(record) => {
              setFocusedHistoryRecordId(record.recordId)
              setPage('history')
            }}
            onNavigateSingle={() => setPage('single-review')}
            onNavigateBatch={() => setPage('batch-review')}
            onNavigateScheduled={() => setPage('scheduled-tasks')}
            onNavigateValidation={() => setPage('validation-set')}
            onNavigateHistory={() => setPage('history')}
          />
          </KeptPage>
        ) : null}

        {keep('single-review') ? (
          <KeptPage active={page === 'single-review'}>
            <SingleReview
              apiDocs={apiDocs}
              examples={examples}
              onNotice={handleNotice}
              prefillCase={singlePrefill}
              onPrefillConsumed={() => setSinglePrefill(null)}
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

        {keep('scheduled-tasks') ? (
          <KeptPage active={page === 'scheduled-tasks'}>
            <ScheduledTasks onNotice={handleNotice} />
          </KeptPage>
        ) : null}

        {keep('validation-set') ? (
          <KeptPage active={page === 'validation-set'}>
          <ValidationSet
            examples={examples}
            onNotice={handleNotice}
            onCopyToSingle={(validationCase) => {
              setSinglePrefill(validationCase)
              setPage('single-review')
            }}
          />
          </KeptPage>
        ) : null}

        {keep('history') ? (
          <KeptPage active={page === 'history'}>
          <History
            items={history}
            selectedRecordId={focusedHistoryRecordId}
            onSelectedRecordConsumed={() => setFocusedHistoryRecordId(undefined)}
            onReuse={(record) => {
              setStandardPrefill(record)
              setPage('single-review')
            }}
            onNotice={handleNotice}
            onDeleted={(recordId) => {
              setHistory((current) => current.filter((item) => item.recordId !== recordId))
              if (focusedHistoryRecordId === recordId) setFocusedHistoryRecordId(undefined)
            }}
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
            <Settings health={health} />
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
          onNavigate={setPage}
          mockMode={mockMode}
          claudeReady={claudeReady}
          pool={pool}
          headerActions={headerActions}
        >
          {renderPage()}
        </AppLayout>
        <FloatButton.BackTop />
      </AntdApp>
    </ConfigProvider>
  )
}
