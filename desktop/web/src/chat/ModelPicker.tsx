/**
 * Which model answers, chosen where the talking happens.
 *
 * It sits under the composer rather than behind a settings panel because it is
 * a per-conversation decision, not configuration: the same deck written by a
 * local CLI agent and by Opus reads differently, and the person deciding is
 * the one about to type.
 *
 * One flat list rather than provider-then-model, because for four of the five
 * providers the pair is the whole choice, and for the local CLI the "model" is
 * simply which CLI answers. Anything the list cannot express — a gateway's
 * address, a model id newer than this build — stays in settings.
 *
 * Choosing restarts the backend. That is not hidden: the picker disables while
 * it happens and the transcript says so, because a restart mid-render would
 * lose the render.
 */

import { useEffect, useState } from 'react'

import * as api from '../api'
import type { Connection, ModelPrefs, ProviderInfo } from '../api'

const NONE = ''

export function ModelPicker({
  disabled,
  onSwitched,
}: {
  disabled: boolean
  onSwitched: (connection: Connection, describe: string) => void
}) {
  const [catalogue, setCatalogue] = useState<Awaited<ReturnType<typeof api.catalogue>> | null>(null)
  const [prefs, setPrefs] = useState<ModelPrefs>({ provider: '', model: '', base_url: '' })
  const [switching, setSwitching] = useState(false)
  const [problem, setProblem] = useState<string | null>(null)

  useEffect(() => {
    void api.catalogue().then(setCatalogue).catch(() => setCatalogue(null))
    void api.modelPrefs().then(setPrefs)
  }, [])

  if (!catalogue) return null

  async function choose(value: string) {
    const [provider, model = ''] = value ? value.split('/') : [NONE]
    const next = { ...prefs, provider, model }
    setPrefs(next)
    setSwitching(true)
    setProblem(null)
    try {
      onSwitched(await api.saveModelPrefs(next), describe(catalogue!.providers, next))
    } catch (error) {
      setProblem((error as Error).message)
    } finally {
      setSwitching(false)
    }
  }

  const current = prefs.provider ? `${prefs.provider}/${prefs.model}` : NONE

  return (
    <div className="picker">
      <select
        className="picker__select"
        value={current}
        disabled={disabled || switching}
        onChange={(e) => void choose(e.target.value)}
      >
        <option value={NONE}>不用模型 · 讲稿我自己写</option>
        {catalogue.providers.map((provider) => (
          <optgroup key={provider.id} label={provider.label}>
            {(catalogue.models[provider.id] ?? []).map((model) => (
              <option key={model.id} value={`${provider.id}/${model.id}`}>
                {model.label}
                {provider.needs_key ? '' : ' · 不要 Key'}
              </option>
            ))}
          </optgroup>
        ))}
      </select>
      {switching && <span className="muted">正在切换…</span>}
      {problem && <span style={{ color: '#b0562f', fontSize: 12 }}>{problem}</span>}
    </div>
  )
}

function describe(providers: ProviderInfo[], prefs: ModelPrefs): string {
  if (!prefs.provider) return '好，讲稿由你来写。留空的页会是占位文本。'
  const provider = providers.find((p) => p.id === prefs.provider)
  const needsKey = provider?.needs_key ? '（记得在设置里填 Key）' : ''
  return `换成 ${provider?.label ?? prefs.provider}｜${prefs.model}${needsKey}，之后留空的页我来写。`
}
