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
   * `drafting` is the model writing it right now — non-null only for as long
   * as that takes. */
  deck: {
    /** The document all of these fields describe. */
    projectId: string | null
    written: number
    locked: boolean
    generated: boolean
    drafting: {
      done: number
      total: number
      writing: string
      jobId: string
      rewriting: boolean
    } | null
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

              {/* The pages live in the panel; the decision stays here. One
                  gate is worth keeping visible in the conversation, because
                  everything before it takes seconds and everything after it
                  takes minutes — and a button in a side panel is not where
                  anyone looks for the thing they were asked to confirm. */}
              {message.kind === 'deck' && message.projectId === deck.projectId && (
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
                  {deck.drafting ? (
                    /* Still being written. The button is not shown rather than
                       shown disabled: there is nothing to start yet, and a
                       greyed 「开始生成」 invites the click it will refuse. */
                    <span className="deckgate__writing">
                      <span className="spinner" />
                      <span className="muted">
                        {/* Two numbers, because they answer different
                            questions: how far it has got, and whether it is
                            doing anything right now. A deck is written a few
                            pages per model call and a call takes a minute or
                            more, so the finished count alone sits still long
                            enough to look stuck. */}
                        {`${deck.drafting.rewriting ? '已重写' : '已写'} `}
                        {`${deck.drafting.done} / ${deck.drafting.total} 页`}
                        {deck.drafting.writing && `，正在写${deck.drafting.writing}`}
                      </span>
                      {/* Writing a deck is minutes of model time, and it was
                          the one long wait with no way out of it. */}
                      {deck.drafting.jobId && (
                        <button
                          type="button"
                          className="rule__reset"
                          onClick={() => onStop(deck.drafting!.jobId)}
                        >
                          中止
                        </button>
                      )}
                      <span className="bar">
                        <div
                          className="bar__fill"
                          style={{
                            width: `${Math.round((deck.drafting.done / Math.max(deck.drafting.total, 1)) * 100)}%`,
                          }}
                        />
                        {/* The batch in flight, sweeping past the finished
                            part: the pages it covers are not written yet, so
                            they are not counted — but the work is real and the
                            bar should not be still while it happens. */}
                        <div className="bar__fill bar__fill--pulse" />
                      </span>
                    </span>
                  ) : (
                    <>
                      <span className="muted">
                        {/* Two buttons, two different waits. Writing takes as
                            long as the model takes; rendering takes minutes
                            and cannot be taken back. Rolling them into one
                            「开始生成」 meant the words were written by the
                            time anyone saw the boxes, so writing your own page
                            was overwriting rather than filling in. */}
                        {/* Who wrote which page is not tracked, and now that
                            the model drafts them all up front, guessing gets
                            it wrong: 「你写了 9 页」 of nine pages nobody
                            touched is worse than saying nothing about
                            authorship at all. */}
                        {!message.hasModel
                          ? `已写 ${deck.written} / ${message.pages.length} 页，没写的会是占位文本`
                          : deck.written >= message.pages.length
                            ? // Every page has words on it, whoever wrote them.
                              '讲稿在右侧，改完点开始生成'
                            : deck.written === 0
                              ? '想自己写的页先写在右侧，剩下的点「生成讲稿」补齐'
                              : `你写了 ${deck.written} / ${message.pages.length} 页，剩下的可以让模型补`}
                      </span>
                      {/* Three states, one button. With every page written
                          it used to disappear, which left 「重新生成」 — the
                          whole film, from the voice down — as the only thing
                          to press, and no way at all to ask for the script to
                          be written again. */}
                      {message.hasModel && (
                        <button
                          type="button"
                          className="deckgate__draft"
                          disabled={deck.locked}
                          onClick={deck.onDraft}
                        >
                          {deck.written === 0
                            ? '生成讲稿'
                            : deck.written < message.pages.length
                              ? `补齐剩下 ${message.pages.length - deck.written} 页`
                              : '重写讲稿'}
                        </button>
                      )}
                      <button
                        type="button"
                        className="deckgate__go"
                        disabled={deck.locked}
                        onClick={deck.onRender}
                      >
                        {deck.locked ? '已开始' : deck.generated ? '重新生成' : '开始生成'}
                      </button>
                    </>
                  )}
                </div>
              )}
              {/* An earlier document in the same conversation: still openable,
                  but the gate below belongs to the one being worked on now. */}
              {message.kind === 'deck' && message.projectId !== deck.projectId && (
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
        <div ref={end} />
      </div>
    </div>
  )
}
