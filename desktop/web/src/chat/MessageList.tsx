/** The transcript. Scrolls itself as it grows. */

import { useEffect, useRef } from 'react'

import { JobCard } from './JobCard'
import type { Message } from './types'

export function MessageList({
  messages,
  onShow,
  onStop,
  deck,
}: {
  messages: Message[]
  /** Open the artifacts panel on this project. */
  onShow: (projectId: string) => void
  /** Ask the running job to stop. */
  onStop: (jobId: string) => void
  /** The gate: how much of the script is written, and how to start.
   *
   * Writing the script reports through the same card a render does, in a line
   * of its own below — one wait, one presentation of it. */
  deck: {
    /** The document all of these fields describe. */
    projectId: string | null
    /** How many pages it has, so the bar can talk about them. */
    pages: number
    hasModel: boolean
    written: number
    locked: boolean
    /** Something is running: the buttons are greyed but say what they say. */
    busy: boolean
    generated: boolean
    onRender: () => void
    /** Write the pages nobody has written, keeping the ones they have. */
    onDraft: () => void
  }
}) {
  const end = useRef<HTMLDivElement>(null)
  useEffect(() => {
    end.current?.scrollIntoView({ behavior: 'smooth', block: 'end' })
  }, [messages])

  return (
    <div className="transcript">
      <div className="column">
        {messages.map((message) => (
          <div
            key={message.id}
            className={`turn turn--${message.role}`}
          >
            <div className="turn__body">
              {/* A turn that is still being worked on says so with the same
                  ring the progress card uses, so the wait never looks like a
                  reply that simply stopped. */}
              {message.pending ? (
                <span className="thinking">
                  <span className="spinner" />
                  {message.text}
                </span>
              ) : (
                message.text
              )}
              {message.kind === 'text' && message.file && (
                <div>
                  <span className="turn__file">{message.file}</span>
                </div>
              )}

              {/* Every deck card is a record of what was said: the document,
                  openable. What can be *done* is not here — it moved to the
                  bar under the last reply, because a row of buttons pinned
                  half a screen up is a row of buttons about a conversation
                  that has moved on. */}
              {message.kind === 'deck' && (
                <div className="deckgate">
                  <button
                    type="button"
                    className="turn__artifact"
                    onClick={() => onShow(message.projectId)}
                  >
                    {'文档 · '}
                    {message.pages.length}
                    {' 页'}
                  </button>
                </div>
              )}
              {message.kind === 'job' && (
                <JobCard job={message.job} onStop={message.job ? () => onStop(message.job!.job_id) : undefined} />
              )}
              {/* A reference, not the thing itself. Unrolling a player, a
                  quality report and a thirty-entry ledger into the middle of
                  the conversation pushed the reply a screen and a half away
                  and scrolled the video out of reach at the next message. */}
              {message.kind === 'video' && (
                <button
                  type="button"
                  className="turn__artifact"
                  onClick={() => onShow(message.projectId)}
                >
                  成片 · {message.scenes.length} 个场景 ·{' '}
                  {Math.round(message.scenes.reduce((sum, s) => sum + s.duration, 0))} 秒
                  {message.quality && ` · 质量分 ${message.quality.score}`}
                </button>
              )}
            </div>
          </div>
        ))}

        {/* What can be done next, under the last thing said — the way a chat
            offers its actions. Gone while something is running: the only
            thing to press then is 「中止」, and it is on the progress card.
            Back when the run ends, whether it finished or was stopped. */}
        {deck.projectId && deck.pages > 0 && !deck.busy && (
          <div className="actionbar">
            <span className="muted actionbar__hint">
              {!deck.hasModel
                ? `已写 ${deck.written} / ${deck.pages} 页，没写的会是占位文本`
                : deck.written === 0
                  ? '想自己写的页先写在右侧，剩下的让模型补'
                  : deck.written < deck.pages
                    ? `你写了 ${deck.written} / ${deck.pages} 页`
                    : '讲稿在右侧，改完就能出片'}
            </span>
            {deck.hasModel && (
              <button type="button" className="deckgate__draft" onClick={deck.onDraft}>
                {deck.written === 0
                  ? '生成讲稿'
                  : deck.written < deck.pages
                    ? `补齐剩下 ${deck.pages - deck.written} 页`
                    : '重写讲稿'}
              </button>
            )}
            <button type="button" className="deckgate__go" onClick={deck.onRender}>
              {deck.generated ? '重新生成' : '开始生成'}
            </button>
          </div>
        )}
        <div ref={end} />
      </div>
    </div>
  )
}
