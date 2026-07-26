import { Button, Space, Tabs, message } from 'antd'
import { useEffect, useMemo, useRef } from 'react'

declare global {
  interface Window {
    marked?: {
      parse: (markdown: string, options?: Record<string, unknown>) => string
    }
    hljs?: {
      highlightElement: (element: HTMLElement) => void
    }
  }
}

interface MarkdownViewerProps {
  content: string
  fileName?: string
}

const STANDARD_LOGIC_TYPE_LABELS: Record<string, string> = {
  time_filter: '时间处理与筛选',
  condition_filter: '条件筛选',
  iteration: '列表遍历',
  aggregation: '聚合计算',
  derivation: '衍生计算',
  branch: '条件分支',
}

export function isJsonContent(content?: string) {
  if (!content?.trim()) return false
  try {
    JSON.parse(content)
    return true
  } catch {
    return false
  }
}

export function isMarkdownContent(content?: string, fileName?: string) {
  const name = String(fileName || '').toLowerCase()
  if (name.endsWith('.md') || name.endsWith('.markdown')) return true
  const text = content || ''
  return /(^|\n)#{1,6}\s+\S/.test(text)
    || /(^|\n)\s*[-*]\s+\S/.test(text)
    || /(^|\n)\s*\d+\.\s+\S/.test(text)
    || /```/.test(text)
    || /\n\s*\|?.*?\|\s*\n\s*\|?\s*:?-{3,}:?\s*(\|\s*:?-{3,}:?\s*)+\|?/.test(text)
    || /\*\*[^*]+\*\*/.test(text)
}

function escapeHtml(value: string): string {
  return value
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;')
    .replace(/'/g, '&#39;')
}

function inlineMarkdown(value: string): string {
  return escapeHtml(value)
    .replace(/&lt;br\s*\/?&gt;/gi, '<br />')
    .replace(/\*\*([^*]+)\*\*/g, '<strong>$1</strong>')
    .replace(/`([^`]+)`/g, '<code>$1</code>')
}

function splitTableRow(row: string): string[] {
  const trimmed = row.trim()
  const source = trimmed.startsWith('|') ? trimmed.slice(1, trimmed.endsWith('|') ? -1 : undefined) : trimmed
  const cells: string[] = []
  let current = ''
  let escaped = false
  for (const char of source) {
    if (escaped) {
      current += char === '|' ? '|' : `\\${char}`
      escaped = false
      continue
    }
    if (char === '\\') {
      escaped = true
      continue
    }
    if (char === '|') {
      cells.push(current)
      current = ''
      continue
    }
    current += char
  }
  if (escaped) current += '\\'
  cells.push(current)
  return cells
}

function renderTable(lines: string[]): string {
  const rows = lines.filter((line) => line.trim().startsWith('|'))
  if (rows.length < 2) return ''
  const header = splitTableRow(rows[0]).map((cell) => inlineMarkdown(cell.trim()))
  const bodyRows = rows.slice(2).map((row) => splitTableRow(row).map((cell) => inlineMarkdown(cell.trim())))
  const cellStyle = 'overflow-wrap:anywhere;word-break:break-word;white-space:normal;'
  return [
    '<table>',
    '<thead><tr>',
    ...header.map((cell) => `<th style="${cellStyle}">${cell}</th>`),
    '</tr></thead>',
    '<tbody>',
    ...bodyRows.map((row) => `<tr>${row.map((cell) => `<td style="${cellStyle}">${cell}</td>`).join('')}</tr>`),
    '</tbody>',
    '</table>',
  ].join('')
}

function renderMarkdownFallback(markdown: string): string {
  const lines = markdown.replace(/\r\n/g, '\n').split('\n')
  const html: string[] = []
  let index = 0
  let inCode = false
  let codeLines: string[] = []
  let listType: 'ul' | 'ol' | null = null

  const closeList = () => {
    if (listType) {
      html.push(`</${listType}>`)
      listType = null
    }
  }

  while (index < lines.length) {
    const line = lines[index]
    if (line.trim().startsWith('```')) {
      if (inCode) {
        html.push(`<pre><code>${escapeHtml(codeLines.join('\n'))}</code></pre>`)
        codeLines = []
        inCode = false
      } else {
        closeList()
        inCode = true
      }
      index += 1
      continue
    }
    if (inCode) {
      codeLines.push(line)
      index += 1
      continue
    }
    if (line.trim().startsWith('|') && lines[index + 1]?.trim().match(/^\|?\s*:?-{3,}:?\s*(\|\s*:?-{3,}:?\s*)+\|?$/)) {
      closeList()
      const tableLines: string[] = []
      while (index < lines.length && lines[index].trim().startsWith('|')) {
        tableLines.push(lines[index])
        index += 1
      }
      html.push(renderTable(tableLines))
      continue
    }
    const ordered = line.match(/^\s*\d+\.\s+(.+)$/)
    const unordered = line.match(/^\s*[-*]\s+(.+)$/)
    if (ordered || unordered) {
      const targetType = ordered ? 'ol' : 'ul'
      if (listType !== targetType) {
        closeList()
        listType = targetType
        html.push(`<${targetType}>`)
      }
      html.push(`<li>${inlineMarkdown((ordered || unordered)?.[1] || '')}</li>`)
      index += 1
      continue
    }
    closeList()
    if (!line.trim()) {
      html.push('<br />')
    } else if (line.startsWith('### ')) {
      html.push(`<h3>${inlineMarkdown(line.slice(4))}</h3>`)
    } else if (line.startsWith('## ')) {
      html.push(`<h2>${inlineMarkdown(line.slice(3))}</h2>`)
    } else if (line.startsWith('# ')) {
      html.push(`<h1>${inlineMarkdown(line.slice(2))}</h1>`)
    } else {
      html.push(`<p>${inlineMarkdown(line)}</p>`)
    }
    index += 1
  }
  closeList()
  if (inCode) {
    html.push(`<pre><code>${escapeHtml(codeLines.join('\n'))}</code></pre>`)
  }
  return html.join('\n')
}

function stripYamlFrontmatter(markdown: string) {
  return String(markdown || '')
    .replace(/^---\r?\n[\s\S]*?\r?\n---[ \t]*(?:\r?\n|$)/, '')
    .trim()
}

function localizeStandardLogicTypes(markdown: string) {
  if (!/(^|\n)##\s+标准加工逻辑\s*(?:\n|$)/.test(markdown)) return markdown
  const typeCodes = Object.keys(STANDARD_LOGIC_TYPE_LABELS).join('|')
  const tableCellPattern = new RegExp('(\\|\\s*)`?(' + typeCodes + ')`?(\\s*\\|)', 'g')
  return markdown.replace(
    tableCellPattern,
    (_match, prefix: string, typeCode: string, suffix: string) =>
      `${prefix}${STANDARD_LOGIC_TYPE_LABELS[typeCode]}${suffix}`,
  )
}

function renderMarkdown(markdown: string): string {
  const cleanMarkdown = localizeStandardLogicTypes(stripYamlFrontmatter(markdown))
  try {
    if (window.marked?.parse) {
      return window.marked.parse(cleanMarkdown, {
        gfm: true,
        breaks: true,
      })
    }
  } catch {
    // 加载失败时继续使用内置解析器，保证文档仍可查看。
  }
  return renderMarkdownFallback(cleanMarkdown)
}

export function MarkdownViewer({ content, fileName }: MarkdownViewerProps) {
  const [messageApi, contextHolder] = message.useMessage()
  const renderedRef = useRef<HTMLDivElement>(null)
  const rendered = useMemo(() => renderMarkdown(content || '暂无内容'), [content])
  const downloadHref = `data:text/markdown;charset=utf-8,${encodeURIComponent(content || '')}`

  useEffect(() => {
    if (!window.hljs || !renderedRef.current) return
    renderedRef.current.querySelectorAll<HTMLElement>('pre code').forEach((element) => {
      window.hljs?.highlightElement(element)
    })
  }, [rendered])

  async function copyRaw() {
    await navigator.clipboard.writeText(content || '')
    messageApi.success('原文已复制')
  }

  return (
    <>
      {contextHolder}
      <Space className="markdown-viewer" direction="vertical" size={12} style={{ width: '100%' }}>
        <Space wrap>
          <Button size="small" onClick={copyRaw}>复制原文</Button>
          <Button size="small" href={downloadHref} download={fileName || 'artifact.md'}>下载</Button>
        </Space>
        <Tabs
          items={[
            {
              key: 'rendered',
              label: '渲染视图',
              children: (
                <div
                  ref={renderedRef}
                  className="markdown-body"
                  dangerouslySetInnerHTML={{ __html: rendered }}
                />
              ),
            },
            {
              key: 'raw',
              label: '原文视图',
              children: (
                <pre className="markdown-raw">
                  {content || '暂无内容'}
                </pre>
              ),
            },
          ]}
        />
      </Space>
    </>
  )
}
