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
import { ChevronIcon } from './Icon'
import type { Connection } from './api'

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
  const [prefs, setPrefs] = useState<api.ModelPrefs>({
    providers: [],
    active: '',
    active_model: '',
  })
  const [catalogue, setCatalogue] = useState<Awaited<ReturnType<typeof api.catalogue>> | null>(null)
  const [caps, setCaps] = useState<Awaited<ReturnType<typeof api.capabilities>> | null>(null)
  const [update, setUpdate] = useState<api.UpdateInfo | null>(null)
  const [editing, setEditing] = useState<string | null>(null)
  const [adding, setAdding] = useState<api.Provider | null>(null)
  const [saving, setSaving] = useState<string | null>(null)
  const [updating, setUpdating] = useState(false)
  const [error, setError] = useState<string | null>(null)

  useEffect(() => {
    if (!open) return
    void api.configuredKeys().then(setConfigured)
    void api.modelPrefs().then(setPrefs)
    void api.capabilities().then(setCaps).catch(() => setCaps(null))
    void api.catalogue().then(setCatalogue).catch(() => setCatalogue(null))
    void api.checkUpdate().then(setUpdate)
  }, [open])

  if (!open) return null

  /** Write the list, and the key that belongs to one of its entries. */
  async function commit(next: api.ModelPrefs, entry: api.Provider, key: string) {
    setSaving(entry.id)
    setError(null)
    try {
      // The list first: `save_key` refuses an id the list does not contain,
      // which is the check that stops a key being written for an entry that
      // nothing will ever read.
      onReconnected(await api.saveModelPrefs(next))
      setPrefs(next)
      if (key.trim()) onReconnected(await api.saveKey(entry.id, key.trim()))
      setConfigured(await api.configuredKeys())
      setCaps(await api.capabilities())
      setEditing(null)
    } catch (thrown) {
      setError(api.describeError(thrown))
    } finally {
      setSaving(null)
    }
  }

  async function remove(entry: api.Provider) {
    const next = {
      ...prefs,
      providers: prefs.providers.filter((provider) => provider.id !== entry.id),
      // Removing what was answering leaves no model rather than silently
      // promoting one nobody chose.
      active: prefs.active === entry.id ? '' : prefs.active,
      active_model: prefs.active === entry.id ? '' : prefs.active_model,
    }
    setError(null)
    try {
      onReconnected(await api.saveModelPrefs(next))
      setPrefs(next)
      setConfigured(await api.configuredKeys())
      setCaps(await api.capabilities())
    } catch (thrown) {
      setError(api.describeError(thrown))
    }
  }



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
                自己加一条就能用：选协议、填地址和 Key、写模型 id。
                本机装了 Claude Code 或 Codex 的话，加一条「本机 CLI」，不需要 Key。
              </p>

              <ul className="providers">
                {prefs.providers.map((entry) => {
                  const local = entry.protocol === 'agent_cli'
                  const ready = local || configured.includes(entry.id)
                  const protocol = api.PROTOCOLS.find((p) => p.id === entry.protocol)
                  return (
                    <li key={entry.id} className="provider">
                      <div className="provider__row">
                        <span>
                          {entry.name || '未命名'}
                          {/* A dot, not a word: this is a list to scan, and the
                              only question asked of each row is whether it is
                              usable. */}
                          {ready && <span className="provider__dot" title="可用" />}
                          <span className="muted" style={{ marginLeft: 8 }}>
                            {protocol?.label ?? entry.protocol}
                            {entry.models.length > 0 && ` · ${entry.models.length} 个模型`}
                            {entry.id === prefs.active && ' · 使用中'}
                          </span>
                        </span>
                        <span style={{ display: 'flex', gap: 6, flexShrink: 0 }}>
                          <button
                            type="button"
                            className="modal__ghost"
                            onClick={() => setEditing(editing === entry.id ? null : entry.id)}
                          >
                            {editing === entry.id ? '收起' : '编辑'}
                          </button>
                          <button
                            type="button"
                            className="modal__ghost"
                            onClick={() => void remove(entry)}
                          >
                            删除
                          </button>
                        </span>
                      </div>

                      {editing === entry.id && (
                        <ProviderForm
                          entry={entry}
                          configured={configured.includes(entry.id)}
                          detected={catalogue?.models.agent_cli ?? []}
                          saving={saving === entry.id}
                          onCancel={() => setEditing(null)}
                          onSave={(edited, key) =>
                            void commit(
                              {
                                ...prefs,
                                providers: prefs.providers.map((p) =>
                                  p.id === entry.id ? edited : p,
                                ),
                              },
                              edited,
                              key,
                            )
                          }
                        />
                      )}
                    </li>
                  )
                })}
              </ul>

              {adding ? (
                <ul className="providers">
                  <li className="provider">
                    <ProviderForm
                      entry={adding}
                      configured={false}
                      detected={catalogue?.models.agent_cli ?? []}
                      saving={saving === adding.id}
                      onCancel={() => setAdding(null)}
                      onSave={(edited, key) => {
                        setAdding(null)
                        void commit(
                          {
                            ...prefs,
                            providers: [...prefs.providers, edited],
                            // The first one configured becomes the one in use:
                            // adding a model and then having to go and switch
                            // to it is a step nobody wants.
                            active: prefs.active || edited.id,
                            active_model:
                              prefs.active_model || edited.models[0]?.id || '',
                          },
                          edited,
                          key,
                        )
                      }}
                    />
                  </li>
                </ul>
              ) : (
                <button
                  type="button"
                  className="provider__add"
                  onClick={() =>
                    setAdding({
                      id: `p_${Date.now().toString(36)}`,
                      name: '',
                      protocol: 'compatible',
                      base_url: '',
                      models: [],
                    })
                  }
                >
                  ＋ 添加提供方
                </button>
              )}
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

function ProviderForm({
  entry,
  configured,
  detected,
  saving,
  onCancel,
  onSave,
}: {
  entry: api.Provider
  configured: boolean
  /** Which local CLIs this machine has, for the agent_cli protocol. */
  detected: api.ModelInfo[]
  saving: boolean
  onCancel: () => void
  onSave: (edited: api.Provider, key: string) => void
}) {
  const [draft, setDraft] = useState<api.Provider>(entry)
  const [key, setKey] = useState('')
  const local = draft.protocol === 'agent_cli'
  const protocol = api.PROTOCOLS.find((p) => p.id === draft.protocol)

  return (
    <div className="provider__form">
      <label className="provider__label">名称</label>
      <input
        className="provider__input"
        value={draft.name}
        placeholder="随便叫什么，比如 DeepSeek、公司网关"
        onChange={(event) => setDraft({ ...draft, name: event.target.value })}
      />

      <label className="provider__label">协议</label>
      {/* The one field that is a choice rather than a value: these are four
          different SDKs with four different request formats, so the list is
          what the code implements, not what exists in the world. */}
      <select
        className="provider__input"
        value={draft.protocol}
        onChange={(event) => setDraft({ ...draft, protocol: event.target.value })}
      >
        {api.PROTOCOLS.map((option) => (
          <option key={option.id} value={option.id}>
            {option.label}
          </option>
        ))}
      </select>
      {protocol?.note && (
        <p className="muted" style={{ marginTop: -4 }}>
          {protocol.note}
        </p>
      )}

      {!local && (
        <>
          <label className="provider__label">
            Base URL{draft.protocol === 'compatible' ? '' : '（留空用官方地址）'}
          </label>
          <input
            className="provider__input"
            value={draft.base_url}
            placeholder="https://api.deepseek.com/v1"
            onChange={(event) => setDraft({ ...draft, base_url: event.target.value })}
          />

          <label className="provider__label">API Key</label>
          <input
            type="password"
            className="provider__input"
            value={key}
            // Says what leaving it alone means. Keys are write-only — they go
            // to the OS keychain and are never read back into the page.
            placeholder={configured ? '已配置——输入新值可替换' : '粘贴 API Key'}
            onChange={(event) => setKey(event.target.value)}
          />
        </>
      )}

      <label className="provider__label">模型目录</label>
      {local && (
        <p className="muted" style={{ marginTop: -4 }}>
          {detected.map((cli) => `${cli.label}：${cli.note}`).join('；') || '没有检测到本机 CLI'}
        </p>
      )}
      <ModelRows
        models={draft.models}
        // For the local CLIs the id is not free text — it names which CLI
        // answers, and only the ones on this machine can.
        suggest={local ? detected.filter((cli) => cli.installed !== false) : []}
        onChange={(models) => setDraft({ ...draft, models })}
      />

      <div className="provider__actions">
        <button type="button" className="modal__ghost" onClick={onCancel}>
          取消
        </button>
        <button
          type="button"
          className="modal__primary"
          disabled={saving || !draft.name.trim()}
          onClick={() => onSave({ ...draft, name: draft.name.trim() }, key)}
        >
          {saving ? '保存中…' : '保存'}
        </button>
      </div>
    </div>
  )
}

/**
 * The models a provider offers: id on the wire, name on screen.
 *
 * Two columns rather than one, because they answer to different people. A row
 * opens for the note, which is the line the picker shows underneath — the one
 * thing that makes a menu of six model ids readable.
 */
function ModelRows({
  models,
  suggest,
  onChange,
}: {
  models: api.Model[]
  suggest: api.ModelInfo[]
  onChange: (models: api.Model[]) => void
}) {
  const [open, setOpen] = useState<number | null>(null)

  const edit = (index: number, patch: Partial<api.Model>) =>
    onChange(models.map((model, at) => (at === index ? { ...model, ...patch } : model)))

  return (
    <div className="models">
      {models.map((model, index) => (
        <div key={index} className="models__entry">
          <div className="models__row">
            {suggest.length > 0 ? (
              <select
                className="provider__input"
                value={model.id}
                onChange={(event) => {
                  const picked = suggest.find((cli) => cli.id === event.target.value)
                  edit(index, { id: event.target.value, name: model.name || picked?.label || '' })
                }}
              >
                <option value="">选一个</option>
                {suggest.map((cli) => (
                  <option key={cli.id} value={cli.id}>
                    {cli.id}
                  </option>
                ))}
              </select>
            ) : (
              <input
                className="provider__input"
                value={model.id}
                placeholder="模型 id，原样传给提供方"
                onChange={(event) => edit(index, { id: event.target.value })}
              />
            )}
            <input
              className="provider__input"
              value={model.name}
              placeholder="显示名"
              onChange={(event) => edit(index, { name: event.target.value })}
            />
            <button
              type="button"
              className="models__icon"
              title="说明"
              onClick={() => setOpen(open === index ? null : index)}
            >
              <ChevronIcon open={open === index} size={15} />
            </button>
            <button
              type="button"
              className="models__icon"
              title="删除"
              onClick={() => onChange(models.filter((_, at) => at !== index))}
            >
              ✕
            </button>
          </div>
          {open === index && (
            <>
              <label className="provider__label">说明</label>
              <input
                className="provider__input"
                value={model.note}
                placeholder="选择模型时显示在名字下面的一行"
                onChange={(event) => edit(index, { note: event.target.value })}
              />
            </>
          )}
        </div>
      ))}

      <button
        type="button"
        className="modal__ghost"
        onClick={() => onChange([...models, { id: '', name: '', note: '' }])}
      >
        添加模型
      </button>
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
