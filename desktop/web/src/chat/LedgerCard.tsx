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
              {calls.map((one) => (
                <div key={one.seq} className="ledger__call">
                  <span style={{ color: one.status === 'failed' ? '#b0562f' : 'inherit' }}>
                    {one.name}
                  </span>
                  {one.detail && <span className="muted">{one.detail}</span>}
                  <span className="muted" style={{ marginLeft: 'auto' }}>
                    {one.duration_s >= 0.05 ? `${one.duration_s.toFixed(1)}s` : ''}
                  </span>
                </div>
              ))}
            </div>
          )}
          {entry.artifacts.map((artifact, index) => (
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
