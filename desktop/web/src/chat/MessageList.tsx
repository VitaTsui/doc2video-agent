/** The transcript. Scrolls itself as it grows. */

import { useEffect, useRef } from 'react'

import { JobCard } from './JobCard'
import type { Message } from './types'

export function MessageList({
  messages,
  onShow,
  deck,
}: {
  messages: Message[]
  /** Open the artifacts panel on this project. */
  onShow: (projectId: string) => void
  /** The gate: how much of the script is written, and how to start. */
  deck: { written: number; locked: boolean; onRender: () => void }
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
              {message.text}
              {message.kind === 'text' && message.file && (
                <div>
                  <span className="turn__file">{message.file}</span>
                </div>
              )}

              {/* The pages live in the panel; the decision stays here. One
                  gate is worth keeping visible in the conversation, because
                  everything before it takes seconds and everything after it
                  takes minutes — and a button in a side panel is not where
                  anyone looks for the thing they were asked to confirm. */}
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
                  <span className="muted">
                    {message.hasModel
                      ? deck.written
                        ? `你写了 ${deck.written} 页，其余由模型补`
                        : '全部由模型来写'
                      : `已写 ${deck.written} / ${message.pages.length} 页，没写的会是占位文本`}
                  </span>
                  <button
                    type="button"
                    className="deckgate__go"
                    disabled={deck.locked}
                    onClick={deck.onRender}
                  >
                    {deck.locked ? '已开始' : '开始生成'}
                  </button>
                </div>
              )}
              {message.kind === 'job' && <JobCard job={message.job} />}
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
        <div ref={end} />
      </div>
    </div>
  )
}
