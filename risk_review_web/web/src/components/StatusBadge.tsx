import { Tag } from 'antd'
import type { CSSProperties } from 'react'

import { mapVerdictToConclusion } from '../utils/reviewTransform'

interface StatusBadgeProps {
  value?: string
}

export function StatusBadge({ value }: StatusBadgeProps) {
  const label = mapVerdictToConclusion(value)
  const displayLabel: Record<string, string> = {
    pass: '通过',
    fail: '不通过',
    unreviewable: '无法评审',
  }
  const styles: Record<string, CSSProperties> = {
    pass: {
      background: '#edf9f3',
      borderColor: '#b9e6cf',
      color: '#176b4d',
    },
    fail: {
      background: '#fff1f0',
      borderColor: '#ffccc7',
      color: '#a8071a',
    },
    unreviewable: {
      background: '#fff7e6',
      borderColor: '#ffe7ba',
      color: '#ad6800',
    },
  }

  return <Tag style={styles[label] || styles.unreviewable}>{displayLabel[label] || label}</Tag>
}
