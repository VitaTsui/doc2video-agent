/** The transcript. Scrolls itself as it grows. */

import { useEffect, useRef } from 'react'

import { DeckCard } from './DeckCard'
import { JobCard } from './JobCard'
import type { Message } from './types'

export function MessageList({
  messages,
  onRender,
  onShow,
}: {
  messages: Message[]
  onRender: (id: string, narrations: Record<string, string>) => void
  /** Open the artifacts panel on this project. */
  onShow: (projectId: string) => void
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

              {message.kind === 'deck' && (
                <DeckCard message={message} onRender={(n) => onRender(message.id, n)} />
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
