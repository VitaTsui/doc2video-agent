/**
 * What you can do with a reply, once it is finished.
 *
 * Copy, and nothing else. Hidden until the turn is under the pointer: a row of
 * controls under every paragraph is a toolbar, and a transcript with a toolbar
 * per line stops reading as a conversation.
 *
 * A chat also offers 「再说一次」 there, and this had one. It does not belong:
 * the replies here are reports of what was done — the deck that was read, the
 * film that came out — and saying one again would say the same thing, because
 * nothing about it was generated afresh. What is worth doing again is the
 * work, and the work has its own buttons: 「重新生成」, 「重新配音」, and 「重做
 * 本页」 next to the page.
 */

import { useState } from 'react'

export function TurnActions({ text }: { text: string }) {
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
