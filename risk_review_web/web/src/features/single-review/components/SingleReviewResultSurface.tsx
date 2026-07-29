import { Button, Space, Spin, Typography } from 'antd'

import { EmptyState } from '../../../components/EmptyState'
import { ReviewResultPanel } from '../../../components/ReviewResultPanel'
import type { AppNotice, Phase1Response, Phase2Response, ReviewResult } from '../../../types/review'
import { ArtifactDocumentButton } from './ArtifactDocumentButton'

export function SingleReviewResultSurface({
  loading,
  loadingPhase2,
  result,
  phase1,
  phase2,
  onNotice,
  onOpenArtifact,
}: {
  loading: boolean
  loadingPhase2: boolean
  result: ReviewResult | null
  phase1: Phase1Response | null
  phase2: Phase2Response | null
  onNotice: (notice: AppNotice) => void
  onOpenArtifact: (fileId: string) => void
}) {
  return (
    <section className="work-surface single-result-surface" aria-label="评审结果">
      <div className="surface-heading">
        <div>
          <Typography.Title level={5}>评审结果</Typography.Title>
          <Typography.Text type="secondary">评审完成后直接展示报告正文。</Typography.Text>
        </div>
        <Space size={8}>
          <ArtifactDocumentButton fileId={phase1?.standardFileId} title="标准加工规范" onNotice={onNotice} />
          {phase2?.reviewFileId ? <Button href={`/outputs/${encodeURIComponent(phase2.reviewFileId)}`} download={phase2.reviewFileId}>下载评审报告</Button> : null}
        </Space>
      </div>
      <div className="single-result-content">
        {loadingPhase2 ? <Spin /> : result ? (
          <ReviewResultPanel result={result} phase1={phase1} phase2={phase2} onOpenArtifact={onOpenArtifact} />
        ) : (
          <EmptyState title={loading ? '正在生成标准并执行评审…' : '填写左侧内容并开始评审。'} />
        )}
      </div>
    </section>
  )
}
