/**
 * Step two: the script, page by page.
 *
 * This is the gate. Everything before it is seconds and reversible; everything
 * after it costs minutes and, once the audio exists, cannot be shortened
 * without re-voicing. So the character budget is shown per page rather than
 * hidden — it is the only thing standing between "write freely" and a video
 * that misses the requested length.
 *
 * Leaving a page blank is allowed: the backend fills it with placeholder text
 * and reports which pages it had to do that for.
 */

import { useMemo, useState } from 'react'
import { Alert, Button, Card, Collapse, Input, Space, Tag, Typography } from 'antd'
import { TextEllipsis } from '@hsu-react/ui'

import * as api from '../api'
import type { GuideRow, PageView } from '../api'

export function ScriptStep({
  projectId,
  pages,
  guide,
  onSubmitted,
}: {
  projectId: string
  pages: PageView[]
  guide: GuideRow[]
  onSubmitted: (jobId: string) => Promise<void>
}) {
  const [drafts, setDrafts] = useState<Record<string, string>>({})
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)

  const budgets = useMemo(
    () => Object.fromEntries(guide.map((row) => [row.page, row])),
    [guide],
  )
  const totalSeconds = guide.reduce((sum, row) => sum + row.page_seconds, 0)
  const written = Object.values(drafts).filter((text) => text.trim()).length

  async function submit() {
    setBusy(true)
    setError(null)
    try {
      const { job_id } = await api.submitNarrations(projectId, drafts)
      await onSubmitted(job_id)
    } catch (e) {
      setError((e as Error).message)
      setBusy(false)
    }
  }

  return (
    <Space direction="vertical" size="middle" style={{ width: '100%' }}>
      <Alert
        type="info"
        showIcon
        message={`共 ${pages.length} 页，预计成片 ${Math.round(totalSeconds)} 秒`}
        description="没写的页会用占位文本填上，成片能出但那几页没有内容。配了模型的话，留空则由模型代写。"
      />
      {error && <Alert type="error" showIcon message={error} />}

      <Collapse
        defaultActiveKey={pages.length ? [String(pages[0].index)] : []}
        items={pages.map((page) => {
          const budget = budgets[page.index]
          const draft = drafts[page.index] ?? ''
          const over = budget && draft.length > budget.target_chars * 1.15
          return {
            key: String(page.index),
            label: (
              <Space>
                <Typography.Text strong>
                  第 {page.index} 页 · {page.title || '无标题'}
                </Typography.Text>
                <Tag>{page.page_type}</Tag>
                {budget && (
                  <Tag color={over ? 'red' : draft ? 'green' : 'default'}>
                    {draft.length}/{budget.target_chars} 字
                  </Tag>
                )}
              </Space>
            ),
            children: (
              <div style={{ display: 'flex', gap: 16, alignItems: 'flex-start' }}>
                {/* The slide itself, so the script is written against what the
                    viewer will be looking at rather than against a list of
                    extracted strings. */}
                {page.image && (
                  <img
                    src={api.assetUrl(projectId, page.image)}
                    alt={`第 ${page.index} 页`}
                    style={{
                      width: 260,
                      border: '1px solid #f0f0f0',
                      borderRadius: 6,
                      flexShrink: 0,
                    }}
                  />
                )}
                <div style={{ flex: 1 }}>
                {page.elements.length > 0 && (
                  <TextEllipsis
                    style={{ fontSize: 12, color: '#8c8c8c', marginBottom: 8 }}
                    tooltipConfig={{ placement: 'topLeft' }}
                  >
                    页面内容：{page.elements.map((e) => e.text).join(' · ')}
                  </TextEllipsis>
                )}
                <Input.TextArea
                  rows={4}
                  value={draft}
                  placeholder={
                    budget
                      ? `这一页讲 ${budget.target_seconds} 秒左右，约 ${budget.target_chars} 字`
                      : '这一页的讲稿'
                  }
                  onChange={(e) =>
                    setDrafts((prev) => ({ ...prev, [page.index]: e.target.value }))
                  }
                />
                {over && (
                  <Typography.Text type="warning" style={{ fontSize: 12 }}>
                    超出预算较多，成片会比预期长；音频生成后长度就改不动了。
                  </Typography.Text>
                )}
                </div>
              </div>
            ),
          }
        })}
      />

      <Card size="small">
        <Space style={{ width: '100%', justifyContent: 'space-between' }}>
          <Typography.Text type="secondary">
            已写 {written} / {pages.length} 页
          </Typography.Text>
          <Button type="primary" loading={busy} onClick={submit}>
            开始生成
          </Button>
        </Space>
      </Card>
    </Space>
  )
}
