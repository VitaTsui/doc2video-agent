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

import TextEllipsis from '@hsu-react/ui/es/components/TextEllipsis'
import { useState } from 'react'

import * as api from '../api'
import type { GuideRow, PageView } from '../api'

export function DeckCard({
  pages,
  guide,
  hasModel,
  locked,
  projectId,
  drafts,
  onDrafts,
  onRedo,
  redoing,
}: {
  pages: PageView[]
  guide: GuideRow[]
  hasModel: boolean
  /** Set once generation starts, so the card stops accepting edits. */
  locked: boolean
  projectId: string
  /** Held by the caller, because the button that uses them is not here —
      starting a render is a decision, and decisions stay in the conversation
      where the person is looking; this panel is where the pages are read. */
  drafts: Record<string, string>
  onDrafts: (next: Record<string, string>) => void
  /** Redo one page, once there is a video for the rest of them to stay in.
   *  Absent before the first render, when the only thing to press is 开始生成. */
  onRedo?: (page: number) => void
  /** The page being redone right now, if any. */
  redoing?: number | null
}) {
  const [open, setOpen] = useState<number | null>(hasModel ? null : pages[0]?.index)
  const [preview, setPreview] = useState<number | null>(null)
  const budgets = Object.fromEntries(guide.map((row) => [row.page, row]))

  return (
    <div className="card card--flush">
      {pages.map((page) => {
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
              <span style={{ color: 'var(--ink-soft)', flexShrink: 0 }}>第 {page.index} 页</span>
              {/* One line, clipped, with the full title on hover — the
                  library's own component, which is where the tooltip comes
                  from. A long title wrapped to three lines and pushed the
                  budget around it, so a list of thirty pages stopped being a
                  list you could run your eye down. */}
              <TextEllipsis className="deck__title">{page.title || '无标题'}</TextEllipsis>
              <span className="muted" style={{ flexShrink: 0 }}>
                {hasModel && !draft
                  ? `约 ${budget?.target_chars} 字`
                  : `${draft.length}/${budget?.target_chars}`}
              </span>
            </button>

            {expanded && (
              <div className="page-row" style={{ padding: '0 16px 14px' }}>
                {page.image && (
                  // Clickable: at this width the render is a thumbnail, and
                  // deciding what a page should say means being able to read
                  // what is on it.
                  <button
                    type="button"
                    className="page-row__shot"
                    onClick={() => setPreview(page.index)}
                    title="点开放大"
                  >
                    <img src={api.assetUrl(projectId, page.image)} alt={`第 ${page.index} 页`} />
                  </button>
                )}
                <div style={{ flex: 1 }}>
                  <textarea
                    rows={4}
                    value={draft}
                    disabled={locked}
                    placeholder={
                      hasModel
                        ? '留空则由模型来写'
                        : `这一页讲 ${budget?.target_seconds} 秒左右，约 ${budget?.target_chars} 字`
                    }
                    onChange={(e) =>
                      onDrafts({ ...drafts, [page.index]: e.target.value })
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
                  {/* One page, redone where it was edited. The whole film is
                      the alternative and it costs minutes: on a 30-page deck
                      the first render took 263 seconds and redoing one page
                      took 18 — every other page keeps the clip it already
                      has. Without this button that saving had no way to be
                      asked for from the window. */}
                  {onRedo && (
                    <div className="page-row__redo">
                      <button
                        type="button"
                        className="deckgate__draft"
                        disabled={locked || redoing !== null || !draft.trim()}
                        onClick={() => onRedo(page.index)}
                      >
                        {redoing === page.index ? '正在重做…' : '重新生成这一页'}
                      </button>
                      <span className="muted">改完点它，只重做这一页，其余片段不动</span>
                    </div>
                  )}
                </div>
              </div>
            )}
          </div>
        )
      })}

      {preview !== null && (
        // Click anywhere to close: the only thing to do here is look, so
        // there is nothing that wants a target of its own.
        <div className="lightbox" onClick={() => setPreview(null)}>
          <img
            src={api.assetUrl(projectId, pages.find((p) => p.index === preview)?.image ?? '')}
            alt={`第 ${preview} 页`}
          />
        </div>
      )}
    </div>
  )
}
