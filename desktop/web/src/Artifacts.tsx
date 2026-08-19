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
import type { GuideRow, LedgerEntry, PageView, Quality, Scene } from './api'
import { DeckCard } from './chat/DeckCard'
import { LedgerCard } from './chat/LedgerCard'

type Tab = 'deck' | 'video' | 'pages' | 'ledger'

/**
 * The quality dimensions, named in the language the rest of the window is in.
 *
 * They are English in the schema because that is where they are computed and
 * compared; leaving them English on screen made five of the six words in that
 * panel the only ones a reader has to translate.
 */
const DIMENSION: Record<string, string> = {
  completeness: '完整度',
  pacing: '节奏',
  originality: '原创度',
  direction: '镜头',
  subtitles: '字幕',
}

export interface ArtifactSet {
  projectId: string
  scenes: Scene[]
  quality: Quality | null
  ledger: LedgerEntry[]
  /** Whether a render has actually produced a file. */
  rendered: boolean
  /** The parsed deck, when this project has just been read. */
  deck?: { pages: PageView[]; guide: GuideRow[]; hasModel: boolean; locked: boolean }
}

export function Artifacts({
  set,
  open,
  onClose,
  drafts,
  onDrafts,
}: {
  set: ArtifactSet | null
  open: boolean
  onClose: () => void
  drafts: Record<string, string>
  onDrafts: (next: Record<string, string>) => void
}) {
  const [tab, setTab] = useState<Tab>('deck')
  if (!open || !set) return null

  const total = set.scenes.reduce((sum, scene) => sum + scene.duration, 0)
  const has: Record<Tab, boolean> = {
    deck: Boolean(set.deck),
    video: set.rendered,
    pages: set.scenes.length > 0,
    ledger: set.ledger.length > 0,
  }
  // Whichever of these the project actually has. A remembered tab belongs to
  // the project it was chosen in: opening an older one from the sidebar left
  // the panel showing an empty「文档」, because that deck belonged to a parse
  // this project never had.
  const shown: Tab = has[tab] ? tab : (['deck', 'video', 'pages', 'ledger'] as Tab[]).find((t) => has[t]) ?? tab

  return (
    <aside className="panel">
      <div className="panel__head">
        <div className="panel__tabs">
          {/* Only offer what exists. A tab that opens on "还没有" is a tab
              that made someone click to find out there was nothing. */}
          <Tabs tab={shown} setTab={setTab} has={has} />
        </div>
        <button type="button" className="sidebar__icon" title="收起" onClick={onClose}>
          <CloseIcon size={18} />
        </button>
      </div>

      <div className="panel__body">
        {shown === 'deck' && set.deck && (
          <DeckCard
            pages={set.deck.pages}
            guide={set.deck.guide}
            hasModel={set.deck.hasModel}
            locked={set.deck.locked}
            projectId={set.projectId}
            drafts={drafts}
            onDrafts={onDrafts}
          />
        )}

        {shown === 'video' &&
          (set.rendered ? (
            <>
              <video src={api.videoUrl(set.projectId)} controls className="panel__video" />
              <div className="muted" style={{ marginTop: 8 }}>
                {set.scenes.length} 个场景 · {Math.round(total)} 秒
                {set.quality && ` · 质量分 ${set.quality.score}`}
              </div>
              {set.quality?.dimensions.map((dimension) => (
                <div key={dimension.name} className="panel__dim">
                  <span className="muted">{DIMENSION[dimension.name] ?? dimension.name}</span>
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

        {shown === 'pages' &&
          set.scenes.map((scene) => (
            <div key={scene.scene_id} className="panel__page">
              <div className="muted">
                第 {scene.source_page} 页 · {scene.duration.toFixed(1)}s
              </div>
              {/* This page on its own. Checking one page meant scrubbing the
                  whole film to where it starts, which is the slowest possible
                  way to answer "did that one come out right". Lazy: thirty
                  clips fetched to render a list nobody has scrolled to is
                  waste. */}
              {scene.clip && (
                <video
                  src={api.assetUrl(set.projectId, scene.clip)}
                  controls
                  preload="none"
                  className="panel__clip"
                />
              )}
              <div>{scene.narration}</div>
            </div>
          ))}

        {shown === 'ledger' && (
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
    ['deck', '文档'],
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
