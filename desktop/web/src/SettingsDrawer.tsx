/**
 * Model keys and what the backend can currently do.
 *
 * Keys are write-only from here: they go into the OS keychain and are never
 * read back into the page, so this shows "已配置" rather than the value. Saving
 * one restarts the backend — its settings are frozen for the life of the
 * process, so there is no way to apply a change without one.
 */

import { useEffect, useState } from 'react'

import * as api from './api'
import type { Connection } from './api'

const FIELD_HEIGHT = 34

/** The save button is deliberately shorter than the field it sits beside: a
 *  button matched to a text input's height reads as a second input. Centred
 *  against it rather than stretched, so the input keeps its own size. */
const BUTTON_HEIGHT = 26

const FIELD: React.CSSProperties = {
  height: FIELD_HEIGHT,
  border: '1px solid var(--line)',
  borderRadius: 8,
  padding: '0 10px',
  font: 'inherit',
  background: 'var(--surface)',
  color: 'inherit',
}

const VENDORS: [string, string][] = [
  ['ANTHROPIC_API_KEY', 'Anthropic（Claude）'],
  ['OPENAI_API_KEY', 'OpenAI'],
  ['GEMINI_API_KEY', 'Google Gemini'],
  ['D2V_COMPATIBLE_API_KEY', 'OpenAI 兼容通道'],
]

export function SettingsDrawer({
  open,
  onClose,
  onReconnected,
  busy,
}: {
  open: boolean
  onClose: () => void
  onReconnected: (connection: Connection) => void
  /** A render is in flight. Installing restarts the process that owns it. */
  busy: boolean
}) {
  const [configured, setConfigured] = useState<string[]>([])
  const [catalogue, setCatalogue] = useState<Awaited<ReturnType<typeof api.catalogue>> | null>(null)
  const [prefs, setPrefs] = useState<api.ModelPrefs>({ provider: '', model: '', base_url: '' })
  const [caps, setCaps] = useState<Awaited<ReturnType<typeof api.capabilities>> | null>(null)
  const [drafts, setDrafts] = useState<Record<string, string>>({})
  const [saving, setSaving] = useState<string | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [update, setUpdate] = useState<api.UpdateInfo | null>(null)
  const [updating, setUpdating] = useState(false)

  useEffect(() => {
    if (!open) return
    void api.configuredKeys().then(setConfigured)
    void api.capabilities().then(setCaps).catch(() => setCaps(null))
    void api.catalogue().then(setCatalogue).catch(() => setCatalogue(null))
    void api.checkUpdate().then(setUpdate)
    void api.modelPrefs().then(setPrefs)
  }, [open])

  if (!open) return null

  const chosen = catalogue?.providers.find((p) => p.id === prefs.provider)

  async function saveModel(next: api.ModelPrefs) {
    setPrefs(next)
    setSaving('model')
    setError(null)
    try {
      onReconnected(await api.saveModelPrefs(next))
      setCaps(await api.capabilities())
    } catch (e) {
      setError(api.describeError(e))
    } finally {
      setSaving(null)
    }
  }

  async function save(vendor: string) {
    setSaving(vendor)
    setError(null)
    try {
      onReconnected(await api.saveKey(vendor, drafts[vendor] ?? ''))
      setDrafts((prev) => ({ ...prev, [vendor]: '' }))
      setConfigured(await api.configuredKeys())
      setCaps(await api.capabilities())
    } catch (e) {
      setError(api.describeError(e))
    } finally {
      setSaving(null)
    }
  }

  return (
    <>
      <div
        onClick={onClose}
        style={{ position: 'fixed', inset: 0, background: 'rgb(0 0 0 / 20%)' }}
      />
      <aside
        style={{
          position: 'fixed',
          top: 0,
          right: 0,
          bottom: 0,
          width: 420,
          maxWidth: '90vw',
          background: 'var(--bg)',
          borderLeft: '1px solid var(--line)',
          padding: 20,
          overflowY: 'auto',
        }}
      >
        <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center' }}>
          <strong>设置</strong>
          <button type="button" className="topbar__button" onClick={onClose}>
            关闭
          </button>
        </div>

        {update && (
          <div className="card" style={{ marginTop: 12 }}>
            <Row label="版本">
              {update.available ? `${update.current} → ${update.version}` : update.current}
            </Row>
            {update.available && (
              <>
                {update.notes && (
                  <p className="muted" style={{ whiteSpace: 'pre-wrap', marginTop: 8 }}>
                    {update.notes.slice(0, 400)}
                  </p>
                )}
                <button
                  type="button"
                  className="composer__send"
                  style={{ width: 'auto', height: BUTTON_HEIGHT, padding: '0 14px', borderRadius: 7, fontSize: 13, marginTop: 8 }}
                  disabled={busy || updating}
                  onClick={() => {
                    setUpdating(true)
                    api.installUpdate().catch((thrown) => {
                      setError(api.describeError(thrown))
                      setUpdating(false)
                    })
                  }}
                >
                  {updating ? '下载中…' : '更新并重启'}
                </button>
                {/* Installing restarts the shell, and the backend is its
                    child — a render in flight would go with it, minutes of
                    work for a version number. */}
                {busy && <div className="muted" style={{ marginTop: 6 }}>正在生成，等这一次跑完再更新。</div>}
              </>
            )}
          </div>
        )}

        <p className="muted" style={{ marginTop: 12 }}>
          不配 Key 也能用——那样讲稿由你自己写，或者由调用方通过 MCP 传进来。配了模型，留空的页
          就由模型代写。本机装了 Claude Code 或 Codex 的话，不用 Key 也能直接把它们当模型用。
        </p>

        {caps && (
          <div className="card" style={{ marginTop: 12 }}>
            <Row label="模型">
              {caps.llm.available ? `${caps.llm.provider}｜${caps.llm.model}` : '未配置'}
            </Row>
            <Row label="语音">
              {caps.tts.provider === 'silent' ? '无（本平台暂无可用语音）' : caps.tts.provider}
            </Row>
            <Row label="渲染器">
              {Object.entries(caps.renderers)
                .filter(([, info]) => info.available)
                .map(([name]) => name)
                .join('、') || '无'}
            </Row>
          </div>
        )}

        {error && (
          <div className="card" style={{ marginTop: 12, color: '#b0562f' }}>
            {error}
          </div>
        )}

        {/* Only what the picker under the composer cannot express: a gateway's
            address, and a model id newer than this build's list. */}
        {chosen?.needs_base_url && (
          <div style={{ marginTop: 20 }}>
            <label style={{ display: 'block', marginBottom: 6 }}>网关地址</label>
            <input
              style={{ ...FIELD, width: '100%' }}
              value={prefs.base_url}
              placeholder="https://api.deepseek.com"
              disabled={saving === 'model'}
              onChange={(e) => setPrefs({ ...prefs, base_url: e.target.value })}
              onBlur={() => void saveModel(prefs)}
            />
            <label style={{ display: 'block', margin: '10px 0 6px' }}>模型名</label>
            <input
              style={{ ...FIELD, width: '100%' }}
              value={prefs.model}
              placeholder="网关上的模型名"
              disabled={saving === 'model'}
              onChange={(e) => setPrefs({ ...prefs, model: e.target.value })}
              onBlur={() => void saveModel(prefs)}
            />
          </div>
        )}

        <div style={{ marginTop: 20 }}>
          {VENDORS.map(([vendor, label]) => (
            <div key={vendor} style={{ marginBottom: 16 }}>
              <label style={{ display: 'flex', gap: 8, alignItems: 'center', marginBottom: 6 }}>
                {label}
                {configured.includes(vendor) && <span className="muted">已配置</span>}
              </label>
              <div style={{ display: 'flex', gap: 8, alignItems: 'center' }}>
                <input
                  type="password"
                  value={drafts[vendor] ?? ''}
                  placeholder={configured.includes(vendor) ? '重新填写可覆盖，留空清除' : '粘贴 API Key'}
                  onChange={(e) => setDrafts((prev) => ({ ...prev, [vendor]: e.target.value }))}
                  style={{ ...FIELD, flex: 1, minWidth: 0 }}
                />
                <button
                  type="button"
                  className="composer__send"
                  style={{ width: 'auto', height: BUTTON_HEIGHT, padding: '0 14px', borderRadius: 7, fontSize: 13 }}
                  disabled={saving === vendor}
                  onClick={() => save(vendor)}
                >
                  {saving === vendor ? '…' : '保存'}
                </button>
              </div>
            </div>
          ))}
        </div>
      </aside>
    </>
  )
}

function Row({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <div style={{ display: 'flex', gap: 12, marginBottom: 4 }}>
      <span className="muted" style={{ width: 56, flexShrink: 0 }}>
        {label}
      </span>
      <span>{children}</span>
    </div>
  )
}
