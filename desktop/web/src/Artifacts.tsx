/**
 * What the pipeline made, off to one side.
 *
 * These used to live inline: a video player, a quality report and a
 * thirty-entry ledger all unrolled into the middle of the conversation, so the
 * reply you wanted to read was pushed a screen and a half away, and the video
 * scrolled out of reach the moment you said anything else.
 *
 * A conversation and its outputs are two different things to look at, and they
 * want different treatment — one is read once and scrolls away, the other is
 * returned to. So the transcript keeps the words and this keeps the artifacts,
 * where they stay put while the talking continues.
 */

import { useState } from 'react'

import * as api from './api'
import { CloseIcon } from './Icon'
import type { LedgerEntry, Quality, Scene } from './api'
import { LedgerCard } from './chat/LedgerCard'

type Tab = 'video' | 'pages' | 'ledger'

export interface ArtifactSet {
  projectId: string
  scenes: Scene[]
  quality: Quality | null
  ledger: LedgerEntry[]
  /** Whether a render has actually produced a file. */
  rendered: boolean
}

export function Artifacts({
  set,
  open,
  onClose,
}: {
  set: ArtifactSet | null
  open: boolean
  onClose: () => void
}) {
  const [tab, setTab] = useState<Tab>('video')
  if (!open || !set) return null

  const total = set.scenes.reduce((sum, scene) => sum + scene.duration, 0)

  return (
    <aside className="panel">
      <div className="panel__head">
        <div className="panel__tabs">
          <Tabs
            tab={tab}
            setTab={setTab}
            // Only offer what exists. A tab that opens on "还没有" is a tab
            // that made someone click to find out there was nothing.
            has={{ video: set.rendered, pages: set.scenes.length > 0, ledger: set.ledger.length > 0 }}
          />
        </div>
        <button type="button" className="sidebar__icon" title="收起" onClick={onClose}>
          <CloseIcon size={18} />
        </button>
      </div>

      <div className="panel__body">
        {tab === 'video' &&
          (set.rendered ? (
            <>
              <video src={api.videoUrl(set.projectId)} controls className="panel__video" />
              <div className="muted" style={{ marginTop: 8 }}>
                {set.scenes.length} 个场景 · {Math.round(total)} 秒
                {set.quality && ` · 质量分 ${set.quality.score}`}
              </div>
              {set.quality?.dimensions.map((dimension) => (
                <div key={dimension.name} className="panel__dim">
                  <span className="muted">{dimension.name}</span>
                  <div className="bar">
                    <div className="bar__fill" style={{ width: `${dimension.score}%` }} />
                  </div>
                  <span className="muted">{dimension.detail}</span>
                </div>
              ))}
            </>
          ) : (
            <p className="muted">还没有成片。</p>
          ))}

        {tab === 'pages' &&
          set.scenes.map((scene) => (
            <div key={scene.scene_id} className="panel__page">
              <div className="muted">
                第 {scene.source_page} 页 · {scene.duration.toFixed(1)}s
              </div>
              <div>{scene.narration}</div>
            </div>
          ))}

        {tab === 'ledger' && (
          <LedgerCard projectId={set.projectId} entries={set.ledger} startOpen />
        )}
      </div>
    </aside>
  )
}

function Tabs({
  tab,
  setTab,
  has,
}: {
  tab: Tab
  setTab: (next: Tab) => void
  has: Record<Tab, boolean>
}) {
  const all: [Tab, string][] = [
    ['video', '成片'],
    ['pages', '逐页'],
    ['ledger', '账本'],
  ]
  return (
    <>
      {all
        .filter(([key]) => has[key])
        .map(([key, label]) => (
          <button
            key={key}
            type="button"
            className={key === tab ? 'panel__tab panel__tab--on' : 'panel__tab'}
            onClick={() => setTab(key)}
          >
            {label}
          </button>
        ))}
    </>
  )
}
