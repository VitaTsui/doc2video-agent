/** The transcript. Scrolls itself as it grows. */

import { useEffect, useRef } from 'react'

import { DeckCard } from './DeckCard'
import { JobCard } from './JobCard'
import { VideoCard } from './VideoCard'
import type { Message } from './types'

export function MessageList({
  messages,
  onRender,
}: {
  messages: Message[]
  onRender: (id: string, narrations: Record<string, string>) => void
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
              {message.kind === 'video' && <VideoCard message={message} />}
            </div>
          </div>
        ))}
        <div ref={end} />
      </div>
    </div>
  )
}
