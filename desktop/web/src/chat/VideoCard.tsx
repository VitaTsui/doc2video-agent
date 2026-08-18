/** The finished video, its score, and what each page ended up saying. */

import { useState } from 'react'

import * as api from '../api'
import { LedgerCard } from './LedgerCard'
import type { Message } from './types'

export function VideoCard({ message }: { message: Extract<Message, { kind: 'video' }> }) {
  const [open, setOpen] = useState(false)
  const total = message.scenes.reduce((sum, s) => sum + s.duration, 0)

  return (
    <div className="card">
      <video src={api.videoUrl(message.projectId)} controls />
      <div style={{ display: 'flex', gap: 16, marginTop: 10 }}>
        <span className="muted">
          {message.scenes.length} 个场景 · {Math.round(total)} 秒
        </span>
        {message.quality && <span className="muted">质量分 {message.quality.score}</span>}
        <button
          className="topbar__button"
          style={{ marginLeft: 'auto', fontSize: 13 }}
          onClick={() => setOpen((v) => !v)}
        >
          {open ? '收起' : '逐页看看'}
        </button>
      </div>

      <LedgerCard projectId={message.projectId} entries={message.ledger} />

      {open && (
        <div style={{ marginTop: 10, borderTop: '1px solid var(--line)', paddingTop: 10 }}>
          {message.quality?.dimensions.map((d) => (
            <div key={d.name} style={{ display: 'flex', gap: 10, alignItems: 'center' }}>
              <span className="muted" style={{ width: 72 }}>
                {d.name}
              </span>
              <div className="bar" style={{ width: 100 }}>
                <div className="bar__fill" style={{ width: `${d.score}%` }} />
              </div>
              <span className="muted">{d.detail}</span>
            </div>
          ))}
          {message.scenes.map((scene) => (
            <p key={scene.scene_id} style={{ margin: '10px 0 0' }}>
              <span className="muted">
                第 {scene.source_page} 页 · {scene.duration.toFixed(1)}s{' '}
              </span>
              {scene.narration}
            </p>
          ))}
        </div>
      )}
    </div>
  )
}
