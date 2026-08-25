/**
 * What you can do with a reply, once it is finished.
 *
 * Hidden until the turn is under the pointer: a row of controls under every
 * paragraph is a toolbar, and a transcript with a toolbar per line stops
 * reading as a conversation. The one that matters is copy — a reply worth
 * acting on is worth taking somewhere else — and 「再说一次」 for the ones that
 * came out wrong, which otherwise costs retyping the question.
 */

import { useState } from 'react'

export function TurnActions({ text, onRetry }: { text: string; onRetry?: () => void }) {
  const [copied, setCopied] = useState(false)

  return (
    <div className="turnbar">
      <button
        type="button"
        className="turnbar__button"
        onClick={() => {
          void navigator.clipboard
            .writeText(text)
            .then(() => {
              setCopied(true)
              // Long enough to be read, short enough that the button is a
              // button again before the pointer comes back to it.
              window.setTimeout(() => setCopied(false), 1600)
            })
            .catch(() => undefined)
        }}
      >
        {copied ? <TickMark /> : <CopyMark />}
        {copied ? '已复制' : '复制'}
      </button>
      {onRetry && (
        <button type="button" className="turnbar__button" onClick={onRetry}>
          <RetryMark />
          再说一次
        </button>
      )}
    </div>
  )
}

const stroke = {
  fill: 'none',
  stroke: 'currentColor',
  strokeWidth: 1.7,
  strokeLinecap: 'round' as const,
  strokeLinejoin: 'round' as const,
}

const CopyMark = () => (
  <svg width="14" height="14" viewBox="0 0 24 24" aria-hidden="true" {...stroke}>
    <rect x="9" y="9" width="11" height="11" rx="2" />
    <path d="M5 15V6a2 2 0 0 1 2-2h9" />
  </svg>
)

const TickMark = () => (
  <svg width="14" height="14" viewBox="0 0 24 24" aria-hidden="true" {...stroke}>
    <path d="m5 13 4 4L19 7" />
  </svg>
)

const RetryMark = () => (
  <svg width="14" height="14" viewBox="0 0 24 24" aria-hidden="true" {...stroke}>
    <path d="M21 12a9 9 0 1 1-3-6.7" />
    <path d="M21 4v5h-5" />
  </svg>
)
