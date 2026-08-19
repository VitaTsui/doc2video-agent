/**
 * Settings, as a dialog with a nav rather than a drawer full of fields.
 *
 * The drawer this replaces listed every vendor's key as a row of password
 * boxes, all four always visible, whether or not the person had any intention
 * of using them. That is a form, not a set of choices — nothing on it said
 * which provider was in use, which were configured, or what the difference
 * between them was.
 *
 * Here a provider is a row that states its own condition (a dot when it holds
 * a key) and opens in place when you want to change it. Nothing is asked for
 * until it is being edited, and the shape of the list says the thing that
 * matters most: a CLI already on this machine needs no key at all.
 */

import { useEffect, useState } from 'react'

import * as api from './api'
import type { Connection } from './api'

/** Which vendor key belongs to which provider in the catalogue. */
const KEY_OF: Record<string, string> = {
  anthropic: 'ANTHROPIC_API_KEY',
  openai: 'OPENAI_API_KEY',
  gemini: 'GEMINI_API_KEY',
  compatible: 'D2V_COMPATIBLE_API_KEY',
}

type Tab = 'models' | 'general'

export function Settings({
  open,
  busy,
  onClose,
  onReconnected,
}: {
  open: boolean
  /** A render is in flight. Installing an update restarts what owns it. */
  busy: boolean
  onClose: () => void
  onReconnected: (connection: Connection) => void
}) {
  const [tab, setTab] = useState<Tab>('models')
  const [configured, setConfigured] = useState<string[]>([])
  const [catalogue, setCatalogue] = useState<Awaited<ReturnType<typeof api.catalogue>> | null>(null)
  const [caps, setCaps] = useState<Awaited<ReturnType<typeof api.capabilities>> | null>(null)
  const [update, setUpdate] = useState<api.UpdateInfo | null>(null)
  const [editing, setEditing] = useState<string | null>(null)
  const [saving, setSaving] = useState<string | null>(null)
  const [updating, setUpdating] = useState(false)
  const [error, setError] = useState<string | null>(null)

  useEffect(() => {
    if (!open) return
    void api.configuredKeys().then(setConfigured)
    void api.capabilities().then(setCaps).catch(() => setCaps(null))
    void api.catalogue().then(setCatalogue).catch(() => setCatalogue(null))
    void api.checkUpdate().then(setUpdate)
  }, [open])

  if (!open) return null

  async function saveProvider(providerId: string, key: string, baseUrl: string) {
    setSaving(providerId)
    setError(null)
    try {
      const vendor = KEY_OF[providerId]
      // An untouched key box means "leave it alone", not "clear it" — the
      // placeholder says as much, and saving a blank would silently unconfigure
      // a provider someone opened only to look at.
      if (vendor && key.trim()) onReconnected(await api.saveKey(vendor, key.trim()))
      if (baseUrl.trim()) {
        const prefs = await api.modelPrefs()
        onReconnected(await api.saveModelPrefs({ ...prefs, base_url: baseUrl.trim() }))
      }
      setConfigured(await api.configuredKeys())
      setCaps(await api.capabilities())
      setEditing(null)
    } catch (thrown) {
      setError(api.describeError(thrown))
    } finally {
      setSaving(null)
    }
  }

  const providers = catalogue?.providers ?? []

  return (
    <>
      <div className="modal__mask" onClick={onClose} />
      <div className="modal" role="dialog" aria-label="设置">
        <nav className="modal__nav">
          <div className="modal__brand">设置</div>
          <button
            type="button"
            className={tab === 'models' ? 'modal__tab modal__tab--on' : 'modal__tab'}
            onClick={() => setTab('models')}
          >
            模型
          </button>
          <button
            type="button"
            className={tab === 'general' ? 'modal__tab modal__tab--on' : 'modal__tab'}
            onClick={() => setTab('general')}
          >
            环境
          </button>
        </nav>

        <div className="modal__body">
          <div className="modal__topline">
            <button type="button" className="modal__ghost" onClick={onClose}>
              关闭
            </button>
          </div>

          {error && (
            <div className="card" style={{ color: '#b0562f', overflowWrap: 'anywhere' }}>
              {error}
            </div>
          )}

          {tab === 'models' && (
            <>
              <h2 className="modal__title">模型</h2>
              <p className="muted">
                填入提供方的 API Key 即可使用其模型。本机装了 Claude Code 或 Codex 的话，
                不用 Key 也能直接把它们当模型用。
              </p>

              <ul className="providers">
                {providers.map((provider) => {
                  const vendor = KEY_OF[provider.id]
                  const ready = provider.needs_key ? configured.includes(vendor) : true
                  return (
                    <li key={provider.id} className="provider">
                      <div className="provider__row">
                        <span>
                          {provider.label}
                          {/* A dot, not a word: this is a list to scan, and the
                              only question being asked of each row is whether
                              it is usable. */}
                          {ready && <span className="provider__dot" title="可用" />}
                        </span>
                        <button
                          type="button"
                          className="modal__ghost"
                          onClick={() => setEditing(editing === provider.id ? null : provider.id)}
                        >
                          {editing === provider.id ? '收起' : provider.needs_key ? '编辑' : '查看'}
                        </button>
                      </div>

                      {editing === provider.id &&
                        (provider.needs_key ? (
                          <ProviderForm
                            provider={provider}
                            configured={configured.includes(vendor)}
                            saving={saving === provider.id}
                            onCancel={() => setEditing(null)}
                            onSave={(key, baseUrl) => void saveProvider(provider.id, key, baseUrl)}
                          />
                        ) : (
                          <Detected models={catalogue?.models[provider.id] ?? []} />
                        ))}
                    </li>
                  )
                })}
              </ul>
            </>
          )}

          {tab === 'general' && (
            <>
              <h2 className="modal__title">环境</h2>

              {caps && (
                <ul className="providers">
                  <Line label="模型">
                    {caps.llm.available ? `${caps.llm.provider}｜${caps.llm.model}` : '未配置'}
                  </Line>
                  <Line label="语音">
                    {caps.tts.provider === 'silent' ? '无（本平台暂无可用语音）' : caps.tts.provider}
                  </Line>
                  <Line label="渲染器">
                    {Object.entries(caps.renderers)
                      .filter(([, state]) => state.available)
                      .map(([name]) => name)
                      .join('、') || '无'}
                  </Line>
                </ul>
              )}

              {update && (
                <>
                  <h2 className="modal__title" style={{ marginTop: 24 }}>
                    版本
                  </h2>
                  <ul className="providers">
                    <Line label="当前">
                      {update.available ? `${update.current} → ${update.version}` : update.current}
                    </Line>
                  </ul>
                  {update.available && (
                    <>
                      {update.notes && (
                        <p className="muted" style={{ whiteSpace: 'pre-wrap' }}>
                          {update.notes.slice(0, 400)}
                        </p>
                      )}
                      <button
                        type="button"
                        className="modal__primary"
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
                          child — a render in flight would go with it. */}
                      {busy && <div className="muted">正在生成，等这一次跑完再更新。</div>}
                    </>
                  )}
                </>
              )}
            </>
          )}
        </div>
      </div>
    </>
  )
}

/**
 * Which local CLIs this machine actually has.
 *
 * The list used to offer both regardless, so choosing one that was not
 * installed produced a model that failed at its first request and said
 * nothing useful about why. The backend checks the PATH; this shows what it
 * found, including the path itself — on a machine with two shells and two
 * installs, which one answered is the question.
 */
function Detected({ models }: { models: api.ModelInfo[] }) {
  return (
    <div className="provider__form">
      <div className="provider__name">本机检测</div>
      {models.map((model) => (
        <div key={model.id} className="provider__row" style={{ padding: '6px 0' }}>
          <span>
            {model.label}
            {model.installed && <span className="provider__dot" title="已安装" />}
          </span>
          <span className="muted" style={{ overflowWrap: 'anywhere', textAlign: 'right' }}>
            {model.note}
          </span>
        </div>
      ))}
      <p className="muted" style={{ marginBottom: 0 }}>
        装好并登录之后重开设置即可刷新。用它们不消耗 API 额度，但每次调用有固定开销，
        批量出片仍建议配 Key。
      </p>
    </div>
  )
}

function ProviderForm({
  provider,
  configured,
  saving,
  onCancel,
  onSave,
}: {
  provider: api.ProviderInfo
  configured: boolean
  saving: boolean
  onCancel: () => void
  onSave: (key: string, baseUrl: string) => void
}) {
  const [key, setKey] = useState('')
  const [baseUrl, setBaseUrl] = useState('')
  const [advanced, setAdvanced] = useState(provider.needs_base_url)

  return (
    <div className="provider__form">
      <div className="provider__name">
        {provider.label} <span className="muted">{provider.id}</span>
      </div>

      <label className="provider__label">API Key</label>
      <input
        type="password"
        className="provider__input"
        value={key}
        // Says what leaving it alone means. Keys are write-only — they go to
        // the OS keychain and are never read back into the page.
        placeholder={configured ? '已配置——输入新值可替换' : '粘贴 API Key'}
        onChange={(event) => setKey(event.target.value)}
      />

      <button type="button" className="provider__more" onClick={() => setAdvanced((v) => !v)}>
        {advanced ? '▾' : '▸'} 自定义设置
      </button>
      {advanced && (
        <>
          <label className="provider__label">Base URL</label>
          <input
            className="provider__input"
            value={baseUrl}
            placeholder={provider.needs_base_url ? '必填，例如 https://api.example.com/v1' : '留空用默认'}
            onChange={(event) => setBaseUrl(event.target.value)}
          />
        </>
      )}

      <div className="provider__actions">
        <button type="button" className="modal__ghost" onClick={onCancel}>
          取消
        </button>
        <button
          type="button"
          className="modal__primary"
          disabled={saving}
          onClick={() => onSave(key, baseUrl)}
        >
          {saving ? '保存中…' : '保存'}
        </button>
      </div>
    </div>
  )
}

function Line({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <li className="provider">
      <div className="provider__row">
        <span className="muted">{label}</span>
        <span>{children}</span>
      </div>
    </li>
  )
}
