/**
 * The account of how the video got made, openable step by step.
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
  decision: '决定',
  degradation: '降级',
  note: '',
}

export function LedgerCard({ projectId, entries }: { projectId: string; entries: LedgerEntry[] }) {
  const [open, setOpen] = useState(false)
  const total = entries.reduce((sum, e) => sum + e.duration_s, 0)

  if (entries.length === 0) return null

  return (
    <div className="card" style={{ marginTop: 8 }}>
      <button
        type="button"
        className="topbar__button"
        style={{ padding: 0, fontSize: 13 }}
        onClick={() => setOpen((v) => !v)}
      >
        {open ? '收起' : `看看每一步做了什么（${entries.length} 步 · ${Math.round(total)}s）`}
      </button>

      {open && (
        <div style={{ marginTop: 10 }}>
          {entries.map((entry) => (
            <Step key={entry.seq} projectId={projectId} entry={entry} />
          ))}
        </div>
      )}
    </div>
  )
}

function Step({ projectId, entry }: { projectId: string; entry: LedgerEntry }) {
  const [open, setOpen] = useState(false)
  const mark = KIND_MARK[entry.kind] ?? entry.kind
  const failed = entry.status === 'failed'

  return (
    <div style={{ borderTop: '1px solid var(--line)', padding: '8px 0' }}>
      <button
        type="button"
        onClick={() => setOpen((v) => !v)}
        disabled={entry.artifacts.length === 0}
        style={{
          display: 'flex',
          width: '100%',
          gap: 10,
          alignItems: 'baseline',
          border: 'none',
          background: 'transparent',
          font: 'inherit',
          padding: 0,
          textAlign: 'left',
          cursor: entry.artifacts.length ? 'pointer' : 'default',
        }}
      >
        <span style={{ color: failed ? '#b0562f' : 'inherit' }}>
          {mark && <span className="muted">[{mark}] </span>}
          {entry.name}
        </span>
        <span className="muted" style={{ marginLeft: 'auto' }}>
          {entry.artifacts.length > 0 && `${entry.artifacts.length} 项 · `}
          {entry.duration_s >= 0.05 ? `${entry.duration_s.toFixed(1)}s` : ''}
        </span>
      </button>

      {entry.detail && (
        <div className="muted" style={{ whiteSpace: 'pre-wrap', marginTop: 4 }}>
          {entry.detail}
        </div>
      )}

      {open && (
        <div style={{ marginTop: 8, display: 'flex', flexDirection: 'column', gap: 10 }}>
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
