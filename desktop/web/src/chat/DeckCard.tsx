/**
 * The deck, once parsed — and the last chance to change anything cheaply.
 *
 * Two shapes, depending on whether a model is configured. With one, this is a
 * summary and a button. Without one the script boxes are here, because the
 * service genuinely does not author text on its own and a page left blank comes
 * out as placeholder narration.
 *
 * The character budget is shown per page rather than hidden. It is the only
 * thing between writing freely and a video that misses the requested length —
 * and once the audio exists its length is what everything downstream takes its
 * timing from, so it cannot be fixed afterwards.
 */

import { useState } from 'react'

import * as api from '../api'
import type { Message } from './types'

export function DeckCard({
  message,
  onRender,
}: {
  message: Extract<Message, { kind: 'deck' }>
  onRender: (narrations: Record<string, string>) => void
}) {
  const [drafts, setDrafts] = useState<Record<string, string>>({})
  const [open, setOpen] = useState<number | null>(message.hasModel ? null : message.pages[0]?.index)
  const budgets = Object.fromEntries(message.guide.map((row) => [row.page, row]))
  const written = Object.values(drafts).filter((t) => t.trim()).length

  return (
    <div className="card card--flush">
      {message.pages.map((page) => {
        const budget = budgets[page.index]
        const draft = drafts[page.index] ?? ''
        const over = budget && draft.length > budget.target_chars * 1.15
        const expanded = open === page.index
        return (
          <div key={page.index} style={{ borderBottom: '1px solid var(--line)' }}>
            <button
              type="button"
              onClick={() => setOpen(expanded ? null : page.index)}
              style={{
                width: '100%',
                display: 'flex',
                gap: 10,
                alignItems: 'center',
                padding: '10px 16px',
                border: 'none',
                background: 'transparent',
                font: 'inherit',
                cursor: 'pointer',
                textAlign: 'left',
              }}
            >
              <span style={{ color: 'var(--ink-soft)', width: 44 }}>第 {page.index} 页</span>
              <span style={{ flex: 1 }}>{page.title || '无标题'}</span>
              <span className="muted">
                {message.hasModel && !draft
                  ? `约 ${budget?.target_chars} 字`
                  : `${draft.length}/${budget?.target_chars}`}
              </span>
            </button>

            {expanded && (
              <div className="page-row" style={{ padding: '0 16px 14px' }}>
                {page.image && (
                  <img src={api.assetUrl(message.projectId, page.image)} alt={`第 ${page.index} 页`} />
                )}
                <div style={{ flex: 1 }}>
                  <textarea
                    rows={4}
                    value={draft}
                    disabled={message.locked}
                    placeholder={
                      message.hasModel
                        ? '留空则由模型来写'
                        : `这一页讲 ${budget?.target_seconds} 秒左右，约 ${budget?.target_chars} 字`
                    }
                    onChange={(e) =>
                      setDrafts((prev) => ({ ...prev, [page.index]: e.target.value }))
                    }
                    style={{
                      width: '100%',
                      border: '1px solid var(--line)',
                      borderRadius: 8,
                      padding: 10,
                      font: 'inherit',
                      resize: 'vertical',
                      background: 'var(--bg)',
                    }}
                  />
                  {over && (
                    <div className="muted" style={{ color: '#b0562f' }}>
                      比预算长不少，成片会超时长；音频生成后就改不动了。
                    </div>
                  )}
                </div>
              </div>
            )}
          </div>
        )
      })}

      <div
        style={{
          display: 'flex',
          justifyContent: 'space-between',
          alignItems: 'center',
          padding: '12px 16px',
        }}
      >
        <span className="muted">
          {message.hasModel
            ? written
              ? `你写了 ${written} 页，其余由模型补`
              : '全部由模型来写'
            : `已写 ${written} / ${message.pages.length} 页，没写的会是占位文本`}
        </span>
        <button
          type="button"
          className="composer__send"
          disabled={message.locked}
          onClick={() => onRender(drafts)}
          style={{ width: 'auto', padding: '6px 16px', borderRadius: 8 }}
        >
          {message.locked ? '已开始' : '开始生成'}
        </button>
      </div>
    </div>
  )
}
