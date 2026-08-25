/**
 * What a turn produced, as something to open.
 *
 * A deck and a finished film are the two things a turn hands back, and both
 * were a thin outlined pill with a line of text in it — which reads as a
 * button someone forgot to style rather than as a thing you have. What every
 * chat does with an attachment is give it a shape: a mark saying what kind of
 * thing it is, its name, and what is worth knowing about it underneath.
 */

import type { ReactNode } from 'react'

export function Attachment({
  icon,
  title,
  meta,
  onOpen,
}: {
  /** One glyph. A file, a film — enough to tell the two apart at a glance. */
  icon: ReactNode
  title: string
  /** The line under the name: page count, length, score. */
  meta: string
  onOpen: () => void
}) {
  return (
    <button type="button" className="attachment" onClick={onOpen}>
      <span className="attachment__icon">{icon}</span>
      <span className="attachment__text">
        <span className="attachment__title">{title}</span>
        <span className="attachment__meta">{meta}</span>
      </span>
    </button>
  )
}

/** A page with a corner turned. */
export const DeckMark = () => (
  <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor"
    strokeWidth="1.7" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
    <path d="M14 3H7a2 2 0 0 0-2 2v14a2 2 0 0 0 2 2h10a2 2 0 0 0 2-2V8z" />
    <path d="M14 3v5h5" />
  </svg>
)

/** A play triangle in a frame. */
export const FilmMark = () => (
  <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor"
    strokeWidth="1.7" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
    <rect x="3" y="5" width="18" height="14" rx="2" />
    <path d="M10 9.5v5l4.5-2.5z" />
  </svg>
)
