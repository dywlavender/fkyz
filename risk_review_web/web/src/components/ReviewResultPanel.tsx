import type { Phase1Response, Phase2Response, ReviewResult } from '../types/review'
import { EmptyState } from './EmptyState'
import { MarkdownViewer } from './MarkdownViewer'

interface ReviewResultPanelProps {
  result: ReviewResult
  phase1?: Phase1Response | null
  phase2?: Phase2Response | null
  onOpenArtifact?: (fileId: string) => void
}

export function ReviewResultPanel({ result, phase2 }: ReviewResultPanelProps) {
  const markdown = result.rawMarkdown || result.rawText || ''

  if (!markdown.trim()) {
    return <EmptyState title="暂无评审结果 Markdown 文档。" />
  }

  return (
    <MarkdownViewer
      content={markdown}
      fileName={phase2?.reviewFileId || '评审结果.md'}
    />
  )
}
