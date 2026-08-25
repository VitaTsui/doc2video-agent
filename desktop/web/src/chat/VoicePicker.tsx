/**
 * Which voice to say it again in.
 *
 * 「重新配音」 existed and said it again in the same voice, which is the useful
 * half of it — a hung engine, a fix to how the words are broken up — and not
 * the half anyone asks for by name. What they ask for is a different voice,
 * and the only place to choose one was Settings, which sets the machine's
 * default rather than this project's.
 *
 * The list is what this machine can actually speak with. Packs that are not
 * installed are not offered: choosing one would fail at the moment the film is
 * being made, twenty minutes from now.
 */

import { useEffect, useState } from 'react'

import * as api from '../api'

export function VoicePicker({
  onPick,
  onClose,
}: {
  /** Empty means 「same voice, say it again」. */
  onPick: (voice: string) => void
  onClose: () => void
}) {
  const [packs, setPacks] = useState<api.VoicePack[]>([])
  const [current, setCurrent] = useState('')

  useEffect(() => {
    void api
      .voicePacks()
      .then((answer) => {
        setPacks(answer.packs.filter((pack) => pack.installed && pack.voices.length > 0))
        setCurrent(answer.voice)
      })
      .catch(() => undefined)
  }, [])

  return (
    <div className="voices">
      <button type="button" className="voices__one" onClick={() => onPick('')}>
        <span>同一个声音，重念一遍</span>
        <span className="muted">修念错的地方</span>
      </button>
      {packs.map((pack) =>
        pack.voices.map((voice) => (
          <button
            key={`${pack.id}:${voice.id}`}
            type="button"
            className={voice.id === current ? 'voices__one voices__one--on' : 'voices__one'}
            onClick={() => onPick(voice.id)}
          >
            <span>{voice.name}</span>
            <span className="muted">
              {pack.name}
              {voice.gender ? ` · ${voice.gender}` : ''}
              {pack.online ? ' · 需联网' : ''}
            </span>
          </button>
        )),
      )}
      <button type="button" className="voices__cancel muted" onClick={onClose}>
        不换了
      </button>
    </div>
  )
}
