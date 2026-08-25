/**
 * How the video got made, step by step, each one openable.
 *
 * A progress bar says how far along something is; it never says what was
 * produced. This does: every step lists what it made, and each of those opens —
 * the page render, the narration written for it, the audio clip, the camera
 * moves, the rendered segment. When the result is wrong, this is where you find
 * out at which step it went wrong, rather than inferring it from the video.
 *
 * Collapsed by default. Someone whose video came out fine should not have to
 * scroll past thirty entries to reach it.
 */

import { useState } from 'react'

import * as api from '../api'
import type { LedgerEntry } from '../api'

const KIND_MARK: Record<string, string> = {
  stage: '',
  call: '',
  decision: '决定',
  degradation: '降级',
  note: '',
}

export function LedgerCard({
  projectId,
  entries,
  startOpen = false,
}: {
  projectId: string
  entries: LedgerEntry[]
  /** Open from the start when it is the point of the view it sits in. */
  startOpen?: boolean
}) {
  const [open, setOpen] = useState(startOpen)
  // Calls belong under the step that made them. Flat, a thirty-scene render is
  // thirty peers of 「解析文档」 and the shape of the run disappears.
  const steps = entries.filter((entry) => entry.kind !== 'call')
  const calls = new Map<number, LedgerEntry[]>()
  for (const entry of entries) {
    if (entry.kind !== 'call') continue
    const under = calls.get(entry.parent) ?? []
    under.push(entry)
    calls.set(entry.parent, under)
  }
  // Only the steps' own time: a stage already contains its calls, and adding
  // both counts the same seconds twice.
  const total = steps.reduce((sum, e) => sum + e.duration_s, 0)

  if (entries.length === 0) return null

  return (
    <div className="card" style={{ marginTop: 8 }}>
      <button
        type="button"
        className="topbar__button"
        style={{ padding: 0, fontSize: 13 }}
        onClick={() => setOpen((v) => !v)}
      >
        {open
          ? '收起'
          : `看看每一步做了什么（${steps.length} 步 · ${entries.length - steps.length} 次调用 · ${Math.round(total)}s）`}
      </button>

      {open && (
        <div style={{ marginTop: 10 }}>
          {steps.map((entry) => (
            <Step
              key={entry.seq}
              projectId={projectId}
              entry={entry}
              calls={calls.get(entry.seq) ?? []}
            />
          ))}
        </div>
      )}
    </div>
  )
}

function Step({
  projectId,
  entry,
  calls,
}: {
  projectId: string
  entry: LedgerEntry
  /** Every call this step made, in the order it made them. */
  calls: LedgerEntry[]
}) {
  const [open, setOpen] = useState(false)
  const grouped = groupCalls(calls)
  const { claimed, loose } = pairUp(entry, grouped)
  const mark = KIND_MARK[entry.kind] ?? entry.kind
  const failed = entry.status === 'failed'
  const openable = entry.artifacts.length > 0 || calls.length > 0

  return (
    <div className="step">
      <button
        type="button"
        onClick={() => setOpen((v) => !v)}
        disabled={!openable}
        className="step__head"
        style={{ cursor: openable ? 'pointer' : 'default' }}
      >
        {/* Two deliberate lines rather than one row left to wrap. In a panel
            this narrow the single row broke wherever it ran out of space —
            「presentation-」 on one line and 「understanding」 on the next, with
            the timings folded in between. What the step is goes on top; what
            it cost goes underneath. */}
        <span className="step__title">
          <span style={{ color: failed ? '#b0562f' : 'inherit' }}>
            {mark && <span className="muted">[{mark}] </span>}
            {entry.name}
          </span>
          {/* The name in the code, next to the name in the window: 「生成讲稿」
              is what was attempted, `presentation-narration` is what ran. */}
          {entry.skill && <span className="step__skill muted">{entry.skill}</span>}
          {entry.duration_s >= 0.05 && (
            <span className="step__time muted">{`${entry.duration_s.toFixed(1)}s`}</span>
          )}
        </span>
        <span className="step__meta muted">
          {[
            calls.length > 0 ? `${calls.length} 次调用` : '',
            // What actually did the work. 「配音」 says what was attempted;
            // `tts:piper` says what it came out of, and two runs that differ
            // there sound different.
            entry.tools.length > 0 ? entry.tools.join('、') : '',
            entry.artifacts.length > 0 ? `${entry.artifacts.length} 项` : '',
          ]
            .filter(Boolean)
            .join(' · ')}
        </span>
      </button>

      {entry.detail && (
        <div className="muted" style={{ whiteSpace: 'pre-wrap', marginTop: 4 }}>
          {entry.detail}
        </div>
      )}

      {open && (
        <div style={{ marginTop: 8, display: 'flex', flexDirection: 'column', gap: 10 }}>
          {calls.length > 0 && (
            <div className="ledger__calls">
              {grouped.map((one) => (
                <CallRow
                  key={one.seq}
                  projectId={projectId}
                  call={one}
                  made={claimed.get(one.seq) ?? []}
                />
              ))}
            </div>
          )}
          {/* Whatever no call claimed: the step made it itself, or made it
              without a call worth recording. */}
          {loose.map((artifact, index) => (
            <ArtifactView key={index} projectId={projectId} artifact={artifact} />
          ))}
        </div>
      )}
    </div>
  )
}

/**
 * Fold a page's repeated calls into the one thing they produced.
 *
 * A page is spoken in several speech units, each its own call to the same
 * engine, and the clip only exists once they have all been written and
 * joined. Listed one per call, one row carries the audio and the rest carry
 * nothing — which reads as "these ones failed to produce anything", when what
 * actually happened is that six calls made one clip between them.
 *
 * Only consecutive calls of the same tool working on the same thing are
 * folded, so a step that genuinely called the same tool about two different
 * scenes still shows two rows.
 */
function groupCalls(calls: LedgerEntry[]): LedgerEntry[] {
  const grouped: LedgerEntry[] = []
  let parts = 0
  for (const call of calls) {
    const last = grouped[grouped.length - 1]
    const same =
      last &&
      last.name === call.name &&
      call.covers?.length > 0 &&
      String(last.covers) === String(call.covers)
    if (!same) {
      grouped.push({ ...call })
      parts = 1
      continue
    }
    parts += 1
    // The row now stands for all of them: their time added up, and a failure
    // anywhere in the run is the run's failure.
    last.duration_s += call.duration_s
    last.detail = `${parts} 段`
    last.artifacts = [...last.artifacts, ...call.artifacts]
    if (call.status === 'failed') last.status = 'failed'
  }
  return grouped
}

/**
 * Put each output back beside the call that made it.
 *
 * Outputs are collected once, when the step ends, by reading the project — so
 * a thirty-scene render arrives as thirty sub-steps in one block followed by
 * thirty clips in another, and which clip came out of which call is left for
 * the reader to work out from the labels. Every call says what it was working
 * on (`page:7`, `scene:scn_x`) and every output says where it came from; this
 * is the join.
 *
 * Anything unclaimed stays at the step — the 成片, the timeline, the review.
 * Nothing is dropped and nothing is shown twice.
 */
function pairUp(entry: LedgerEntry, calls: LedgerEntry[]) {
  const claimed = new Map<number, LedgerEntry['artifacts']>()
  const taken = new Set<number>()

  for (const call of calls) {
    const mine = call.artifacts.slice()
    if (call.covers?.length) {
      const wanted = new Set(call.covers)
      entry.artifacts.forEach((artifact, index) => {
        if (taken.has(index)) return
        const keys = [
          artifact.scene_id ? `scene:${artifact.scene_id}` : '',
          artifact.page != null ? `page:${artifact.page}` : '',
        ].filter(Boolean)
        if (keys.some((key) => wanted.has(key))) {
          taken.add(index)
          mine.push(artifact)
        }
      })
    }
    if (mine.length > 0) claimed.set(call.seq, mine)
  }

  return { claimed, loose: entry.artifacts.filter((_, index) => !taken.has(index)) }
}

/** One sub-step, with what it produced folded underneath it. */
function CallRow({
  projectId,
  call,
  made,
}: {
  projectId: string
  call: LedgerEntry
  made: LedgerEntry['artifacts']
}) {
  const [open, setOpen] = useState(false)
  const failed = call.status === 'failed'

  return (
    <div className="ledger__callbox">
      <button
        type="button"
        className="ledger__call"
        disabled={made.length === 0}
        style={{ cursor: made.length > 0 ? 'pointer' : 'default' }}
        onClick={() => setOpen((v) => !v)}
      >
        <span className="ledger__tool" style={{ color: failed ? '#b0562f' : 'inherit' }}>
          {call.name}
        </span>
        {/* Broken at its own seams. The sentence is 「第 1 页｜0.0–16.5s｜5 条
            字幕｜1 个动作」 and CJK wraps between any two characters, so a
            narrow panel split it as 「1 个动／作」. Each piece is kept whole and
            the row folds between them instead. */}
        {call.detail && (
          <span className="muted ledger__what">
            {call.detail.split('｜').map((part, index) => (
              <span key={index} className="ledger__part">
                {part}
              </span>
            ))}
          </span>
        )}
        <span className="muted ledger__meta">
          {[
            made.length > 0 ? (open ? '收起' : `${made.length} 项`) : '',
            call.duration_s >= 0.05 ? `${call.duration_s.toFixed(1)}s` : '',
          ]
            .filter(Boolean)
            .join(' · ')}
        </span>
      </button>
      {open && (
        <div className="ledger__made">
          {made.map((artifact, index) => (
            <ArtifactView key={index} projectId={projectId} artifact={artifact} />
          ))}
        </div>
      )}
    </div>
  )
}

function ArtifactView({
  projectId,
  artifact,
}: {
  projectId: string
  artifact: LedgerEntry['artifacts'][number]
}) {
  const url = artifact.path ? api.assetUrl(projectId, artifact.path) : ''

  return (
    <div>
      <div className="muted" style={{ marginBottom: 4 }}>
        {artifact.label}
      </div>
      {artifact.kind === 'image' && <img src={url} alt={artifact.label} style={{ width: 240 }} />}
      {/* Audio and video load lazily: a nine-page deck lists nine clips, and
          fetching them all to render a list nobody has opened yet is waste. */}
      {artifact.kind === 'audio' && <audio src={url} controls preload="none" />}
      {artifact.kind === 'video' && (
        <video src={url} controls preload="none" style={{ maxWidth: 360 }} />
      )}
      {(artifact.kind === 'text' || artifact.kind === 'json') && (
        <div style={{ whiteSpace: 'pre-wrap' }}>{artifact.text}</div>
      )}
    </div>
  )
}
