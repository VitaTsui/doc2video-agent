/** The transcript. Scrolls itself as it grows. */

import { useEffect, useRef } from 'react'

import { readableTitle } from '../naming'
import { Attachment, DeckMark, FilmMark } from './Attachment'
import { Prose } from './Prose'
import { Suggestions } from './Suggestions'
import { TurnActions } from './TurnActions'
import { Thinking } from './Thinking'
import type { Message } from './types'

/** Seconds as a person would say them. */
function _minutes(seconds: number): string {
  const whole = Math.round(seconds)
  if (whole < 60) return `${whole} 秒`
  return `${Math.floor(whole / 60)} 分 ${whole % 60} 秒`
}

export function MessageList({
  messages,
  onShow,
  onRetry,
  onSay,
  deck,
}: {
  messages: Message[]
  /** Open the artifacts panel on this project. */
  onShow: (projectId: string) => void
  /** Ask the same thing again. Absent while something is already running. */
  onRetry?: () => void
  /** Say one of the suggested sentences. */
  onSay?: (text: string) => void
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
    /** Say the existing script again. An empty voice means the same one. */
    onRevoice: (voice: string) => void
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
            {/* Which of the two is speaking, said once at the top of the turn
                rather than left to the indentation. A transcript of bare
                paragraphs is readable only while you remember whose they
                are. */}
            {message.role === 'assistant' && (
              <div className="turn__who" aria-hidden="true">
                <span className="turn__mark">D</span>
              </div>
            )}
            <div className="turn__body">
              {/* A turn that is still being worked on says so with the same
                  ring the progress card uses, so the wait never looks like a
                  reply that simply stopped. */}
              {message.pending ? (
                <span className="thinking">
                  <span className="spinner" />
                  {message.text}
                </span>
              ) : message.role === 'assistant' ? (
                <Prose text={message.text} />
              ) : (
                message.text
              )}
              {message.kind === 'text' && message.file && (
                <div>
                  <span className="turn__file">{readableTitle(message.file)}</span>
                </div>
              )}

              {/* Under the words, on hover. Every chat has this and it is the
                  cheapest useful thing in one: a reply worth acting on is a
                  reply worth copying, and a wrong one is worth asking for
                  again without retyping the question. */}
              {message.role === 'assistant' && !message.pending && message.text && (
                <TurnActions text={message.text} onRetry={onRetry} />
              )}

              {/* Every deck card is a record of what was said: the document,
                  openable. What can be *done* is not here — it moved to the
                  bar under the last reply, because a row of buttons pinned
                  half a screen up is a row of buttons about a conversation
                  that has moved on. */}
              {message.kind === 'deck' && (
                <Attachment
                  icon={<DeckMark />}
                  title={readableTitle(message.file || '') || '这份文档'}
                  meta={`${message.pages.length} 页 · 点开逐页看`}
                  onOpen={() => onShow(message.projectId)}
                />
              )}
              {/* What it is thinking, as it thinks it — the chain, not the
                  filing cabinet. The full account, with every page render and
                  audio clip openable, stays in the panel: a conversation is a
                  place for sentences. How far along it is and the way to stop
                  it live in the chain's own header; a bordered progress card
                  in the middle of a chat is a control panel, and no chat has
                  one. */}
              {message.kind === 'job' && (
                <Thinking
                  entries={message.steps ?? []}
                  job={message.job}
                  settled={message.settled}
                />
              )}
              {/* A reference, not the thing itself. Unrolling a player, a
                  quality report and a thirty-entry ledger into the middle of
                  the conversation pushed the reply a screen and a half away
                  and scrolled the video out of reach at the next message. */}
              {message.kind === 'video' && (
                <Attachment
                  icon={<FilmMark />}
                  title="成片"
                  meta={[
                    `${message.scenes.length} 个场景`,
                    _minutes(message.scenes.reduce((sum, s) => sum + s.duration, 0)),
                    message.quality ? `质量分 ${message.quality.score}` : '',
                  ]
                    .filter(Boolean)
                    .join(' · ')}
                  onOpen={() => onShow(message.projectId)}
                />
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
            {/* Made once already: saying it again is a different job from
                making it again, and much the shorter one — the words and the
                shots stay, only the sound is done over. Before there is a
                film there is nothing to re-voice. */}
            {/* Which voice it is spoken in is a setting, and lives in
                Settings — one place to choose it rather than a second picker
                that would have to agree with the first. This just says it
                again, in whatever voice is chosen there. */}
            {deck.generated && (
              <button type="button" className="deckgate__draft" onClick={() => deck.onRevoice('')}>
                重新配音
              </button>
            )}
            <button type="button" className="deckgate__go" onClick={deck.onRender}>
              {deck.generated ? '重新生成' : '开始生成'}
            </button>
          </div>
        )}

        {/* Under the buttons: what you could say, as something to press. */}
        {deck.projectId && deck.pages > 0 && !deck.busy && onSay && (
          <Suggestions rendered={deck.generated} onSay={onSay} />
        )}
        <div ref={end} />
      </div>
    </div>
  )
}
