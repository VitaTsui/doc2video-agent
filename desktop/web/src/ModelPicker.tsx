/**
 * Which model answers, chosen from what is configured.
 *
 * Drawn here rather than by the component library, for one reason: a model is
 * a name *and* a line saying what it is for, and the library hands its options
 * straight to an antd `Select` with no `optionRender`. A JSX label there would
 * render two lines inside the collapsed field as well — which is the shape
 * that was just taken out of it.
 *
 * So: the provider is the group heading, the model's name is the item, and its
 * note is the line underneath. The field shows the name alone, because by then
 * the choice is made and only one word of it is still worth reading.
 */

import { useEffect, useRef, useState } from 'react'

import type { ModelPrefs } from './api'
import { CaretIcon } from './Icon'

export function ModelPicker({
  prefs,
  disabled,
  onPick,
}: {
  prefs: ModelPrefs
  disabled: boolean
  onPick: (providerId: string, modelId: string) => void
}) {
  const [open, setOpen] = useState(false)
  const box = useRef<HTMLDivElement>(null)

  useEffect(() => {
    if (!open) return
    const close = (event: MouseEvent) => {
      if (!box.current?.contains(event.target as Node)) setOpen(false)
    }
    // Capture: a click on something that stops propagation would otherwise
    // leave the menu hanging open over whatever it just did.
    document.addEventListener('mousedown', close, true)
    return () => document.removeEventListener('mousedown', close, true)
  }, [open])

  const provider = prefs.providers.find((entry) => entry.id === prefs.active)
  const current =
    provider?.models.find((model) => model.id === prefs.active_model) ?? provider?.models[0]

  return (
    <div className="picker" ref={box}>
      <button
        type="button"
        className="picker__button"
        disabled={disabled}
        onClick={() => setOpen((value) => !value)}
      >
        {current?.name || current?.id || '选择模型'}
        <CaretIcon open={open} size={14} />
      </button>

      {open && (
        <div className="picker__menu">
          {prefs.providers.length === 0 && (
            <div className="picker__empty">还没有配置模型，去设置里加一个。</div>
          )}

          {prefs.providers.map((entry) => (
            <div key={entry.id}>
              <div className="picker__group">{entry.name || entry.protocol}</div>
              {entry.models.length === 0 && <div className="picker__empty">这个提供方还没有模型</div>}
              {entry.models.map((model) => {
                const on = entry.id === prefs.active && model.id === current?.id
                return (
                  <button
                    key={model.id}
                    type="button"
                    className="picker__item"
                    onClick={() => {
                      setOpen(false)
                      onPick(entry.id, model.id)
                    }}
                  >
                    <span className="picker__text">
                      <span className="picker__name">{model.name || model.id}</span>
                      {model.note && <span className="picker__note">{model.note}</span>}
                    </span>
                    {on && <span className="picker__tick">✓</span>}
                  </button>
                )
              })}
            </div>
          ))}

          <button
            type="button"
            className="picker__item"
            onClick={() => {
              setOpen(false)
              onPick('', '')
            }}
          >
            <span className="picker__text">
              <span className="picker__name">不用模型</span>
              <span className="picker__note">讲稿我自己写</span>
            </span>
            {!prefs.active && <span className="picker__tick">✓</span>}
          </button>
        </div>
      )}
    </div>
  )
}
