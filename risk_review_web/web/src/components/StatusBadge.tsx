import { Tag } from 'antd'
import type { CSSProperties } from 'react'

import { mapVerdictToConclusion } from '../utils/reviewTransform'

interface StatusBadgeProps {
  value?: string
}

export function StatusBadge({ value }: StatusBadgeProps) {
  const label = mapVerdictToConclusion(value)
  const displayLabel: Record<string, string> = {
    pass: '通过项',
    fail: '不符合项',
    confirm: '待确认项',
    suggestion: '建议项',
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
    confirm: {
      background: '#fff7e6',
      borderColor: '#ffe7ba',
      color: '#ad6800',
    },
    suggestion: {
      background: '#f5f7fa',
      borderColor: '#d8dee8',
      color: '#5f6d80',
    },
  }

  return <Tag style={styles[label] || styles.confirm}>{displayLabel[label] || label}</Tag>
}
