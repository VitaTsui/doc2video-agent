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

// Deep imports, as elsewhere: the barrel pulls ChatList in, and with it
// mermaid, cytoscape and pdf.js — six megabytes of diagram engines for a
// dialog made of text boxes.
import Button from '@hsu-react/ui/es/components/Button'
import Input from '@hsu-react/ui/es/components/Input'
import Select from '@hsu-react/ui/es/components/Select'
import { useEffect, useState } from 'react'

import * as api from './api'
import { ChevronIcon } from './Icon'
import type { Connection } from './api'

type Tab = 'models' | 'voice' | 'plugins' | 'general'

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
            className={tab === 'voice' ? 'modal__tab modal__tab--on' : 'modal__tab'}
            onClick={() => setTab('voice')}
          >
            语音
          </button>
          <button
            type="button"
            className={tab === 'plugins' ? 'modal__tab modal__tab--on' : 'modal__tab'}
            onClick={() => setTab('plugins')}
          >
            插件
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
            <Button size="small" onClick={onClose}>
            关闭
          </Button>
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
                          <Button size="small" onClick={() => setEditing(editing === entry.id ? null : entry.id)}>
            {editing === entry.id ? '收起' : '编辑'}
          </Button>
                          <Button size="small" onClick={() => void remove(entry)}>
            删除
          </Button>
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

          {tab === 'voice' && <VoiceSettings busy={busy} />}

          {tab === 'plugins' && <Plugins />}

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
                      <Button
                        type="primary"
                        size="small"
                        disabled={busy || updating}
                        style={{ marginTop: 8 }}
                        onClick={() => {
                          setUpdating(true)
                          api.installUpdate().catch((thrown) => {
                            setError(api.describeError(thrown))
                            setUpdating(false)
                          })
                        }}
                      >
                        {updating ? '下载中…' : '更新并重启'}
                      </Button>
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
      <Input
        className="provider__input"
        value={draft.name}
        placeholder="随便叫什么，比如 DeepSeek、公司网关"
        onChange={(value) => setDraft({ ...draft, name: value })}
      />

      <label className="provider__label">协议</label>
      {/* The one field that is a choice rather than a value: these are four
          different SDKs with four different request formats, so the list is
          what the code implements, not what exists in the world. */}
      <Select
        className="provider__input"
        value={draft.protocol}
        options={api.PROTOCOLS.map((option) => ({ label: option.label, value: option.id }))}
        onChange={(value) => setDraft({ ...draft, protocol: value as string })}
      />
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
          <Input
            className="provider__input"
            value={draft.base_url}
            placeholder="https://api.deepseek.com/v1"
            onChange={(value) => setDraft({ ...draft, base_url: value })}
          />

          <label className="provider__label">API Key</label>
          <Input.Password
            className="provider__input"
            value={key}
            // Says what leaving it alone means. Keys are write-only — they go
            // to the OS keychain and are never read back into the page.
            placeholder={configured ? '已配置——输入新值可替换' : '粘贴 API Key'}
            onChange={(value) => setKey(value)}
          />
        </>
      )}

      <label className="provider__label">模型目录</label>
      {local && (
        <p className="modal__foot" style={{ margin: '0 0 8px' }}>
          {/* Only what the check actually found. Joining empty notes produced
              「Claude Code：；Codex：」 — a line that looks like a bug because
              it is one. */}
          {detected
            .filter((cli) => cli.note)
            .map((cli) => `${cli.label}：${cli.note}`)
            .join('；') || '没有检测到本机 CLI'}
        </p>
      )}
      <ModelRows
        models={draft.models}
        // For the local CLIs the id is not free text — it names which CLI
        // answers, and only the ones on this machine can.
        suggest={local ? detected.filter((cli) => cli.installed !== false) : []}
        onChange={(models) => setDraft({ ...draft, models })}
      />

      {/* The thing that is true but is not a field. dsh puts one here too —
          「其余字段在 settings.yaml 中」 — and it is the right place for it:
          after everything editable, before the buttons that commit it. */}
      <p className="modal__foot">
        模型 id 原样传给提供方，不做校验——没听说过的 id 更可能是新发布的。
      </p>

      <div className="provider__actions">
        <Button size="small" onClick={onCancel}>
            取消
          </Button>
        <Button
          type="primary"
          size="small"
          disabled={saving || !draft.name.trim()}
          onClick={() => onSave({ ...draft, name: draft.name.trim() }, key)}
        >
          {saving ? '保存中…' : '保存'}
        </Button>
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
              <Select
                className="provider__input"
                value={model.id || undefined}
                placeholder="选一个"
                options={suggest.map((cli) => ({ label: cli.id, value: cli.id }))}
                onChange={(value) => {
                  const picked = suggest.find((cli) => cli.id === value)
                  edit(index, { id: value as string, name: model.name || picked?.label || '' })
                }}
              />
            ) : (
              <Input
                className="provider__input"
                value={model.id}
                placeholder="模型 id，原样传给提供方"
                onChange={(value) => edit(index, { id: value })}
              />
            )}
            <Input
              className="provider__input"
              value={model.name}
              placeholder="显示名"
              onChange={(value) => edit(index, { name: value })}
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
              <Input
                className="provider__input"
                value={model.note}
                placeholder="选择模型时显示在名字下面的一行"
                onChange={(value) => edit(index, { note: value })}
              />
            </>
          )}
        </div>
      ))}

      <Button size="small" onClick={() => onChange([...models, { id: '', name: '', note: '' }])}>
        添加模型
      </Button>
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

/**
 * Which voice the videos are spoken in, and how to get a better one.
 *
 * The packs differ in kind — one is built into the system, one is a model that
 * has to be downloaded, one runs on someone else's computer — and a person
 * choosing a voice does not care about that. What they do care about is stated
 * on every row: what it costs to install, and whether it needs the network. A
 * product that works on a train is a different product, and that is theirs to
 * decide rather than ours to decide quietly.
 */
/**
 * What is inside this build, step by step, and what works on this machine.
 *
 * Two questions get asked and only one of them is a feature list: what a step
 * does is the same everywhere, and whether its tools are usable *here* changes
 * with what is installed. Both are shown together, in the order the steps run,
 * because that is the order in which a missing piece shows up in the video.
 */
function Plugins() {
  const [steps, setSteps] = useState<api.PipelineStep[] | null>(null)
  const [open, setOpen] = useState('')
  const [failure, setFailure] = useState('')

  useEffect(() => {
    void api
      .plugins()
      .then(setSteps)
      .catch((error: Error) => setFailure(api.describeError(error)))
  }, [])

  return (
    <>
      <h2 className="modal__title">插件</h2>
      <p className="muted">
        一条流水线，从上到下跑完就是一支视频。每一步用什么、这台机器上能不能用，都在下面；
        写着「看提示词」的，点开就是原文——不是转述，转述这件事没法拿来对照结果。
      </p>

      {failure && <p className="modal__error">{failure}</p>}
      {steps === null && !failure && <p className="muted">正在看这台机器装了什么…</p>}

      {steps?.map((step) => (
        <div key={step.id} className="pack">
          <div className="pack__head">
            <span className="pack__name">{step.name}</span>
            {/* The name in the window next to the name in the code: 「配音」 is
                what it is called here, `presentation-voice` is what runs. */}
            {step.skill && <span className="step__skill muted">{step.skill}</span>}
            {(step.prompt || step.rules.length > 0) && (
              <button
                type="button"
                className="pack__open"
                onClick={() => setOpen(open === step.id ? '' : step.id)}
              >
                {open === step.id ? '收起' : step.prompt ? '看提示词' : '看规则'}
              </button>
            )}
          </div>
          <div className="muted pack__note">{step.what}</div>

          {open === step.id && step.rules.length > 0 && (
            <div className="rules">
              {step.rules.map((rule) => (
                <div key={rule.name} className="rule">
                  <span className="rule__name">{rule.name}</span>
                  <span className="rule__value">{rule.value}</span>
                  <span className="muted rule__what">{rule.what}</span>
                </div>
              ))}
            </div>
          )}
          {/* The instructions as they are sent, not a summary of them: a
              paraphrase is the thing you cannot check the output against. */}
          {open === step.id && step.prompt && <pre className="prompt">{step.prompt}</pre>}
          {step.parts.map((part) => (
            <div key={part.id} className="part">
              <span className={part.available ? 'part__dot part__dot--on' : 'part__dot'} />
              <span className="part__name">{part.name}</span>
              <span className="muted part__what">
                {part.what}
                {!part.available && part.reason && `（${part.reason}）`}
              </span>
            </div>
          ))}
        </div>
      ))}
    </>
  )
}

/**
 * The published Piper voices: search, and install the one you want.
 *
 * Piper is the one pack that is a file format, and its voices live in a
 * HuggingFace repository — 174 of them, indexed with a size and an MD5 each.
 * Finding one used to mean knowing that and reading a 240KB JSON by hand.
 *
 * The index is cached by the backend after the first look, so typing here is
 * a local search rather than a request per keystroke.
 */
function PiperBrowser({ onInstalled }: { onInstalled: () => void }) {
  const [query, setQuery] = useState('')
  const [found, setFound] = useState<{ matched: number; voices: api.PiperVoice[] } | null>(null)
  const [getting, setGetting] = useState('')
  const [failure, setFailure] = useState('')

  useEffect(() => {
    let live = true
    // A short wait rather than a request per keystroke: the index is on the
    // backend's disk, but the round trip is still a round trip.
    const timer = window.setTimeout(() => {
      void api
        .piperVoices(query)
        .then((result) => live && setFound(result))
        .catch((error: Error) => live && setFailure(api.describeError(error)))
    }, 250)
    return () => {
      live = false
      window.clearTimeout(timer)
    }
  }, [query])

  const install = async (voice: api.PiperVoice) => {
    setFailure('')
    setGetting(voice.key)
    try {
      await api.installPiperVoice(voice.key)
      setFound(await api.piperVoices(query))
      onInstalled()
    } catch (error) {
      setFailure(api.describeError(error))
    } finally {
      setGetting('')
    }
  }

  return (
    <div className="browse">
      <input
        className="browse__search"
        value={query}
        placeholder="搜音色：中文、zh、Chinese、huayan…"
        onChange={(event) => setQuery(event.target.value)}
      />
      {failure && <p className="modal__error">{failure}</p>}
      {found && (
        <>
          <div className="muted browse__count">{`${found.matched} 个音色`}</div>
          {found.voices.map((voice) => (
            <div key={voice.key} className="browse__row">
              <span className="browse__name">{voice.key}</span>
              <span className="muted browse__lang">
                {voice.language_name || voice.language_english}
                {voice.country && ` · ${voice.country}`}
              </span>
              {voice.installed ? (
                <span className="pack__tag pack__tag--on">已装</span>
              ) : (
                <Button
                  size="small"
                  loading={getting === voice.key}
                  disabled={Boolean(getting)}
                  onClick={() => void install(voice)}
                >
                  {`下载 ${Math.max(1, Math.round(voice.size / 1024 / 1024))}MB`}
                </Button>
              )}
            </div>
          ))}
        </>
      )}
    </div>
  )
}

/** The engine and voice a video would be made with right now, as one line. */
function inUse(state: Awaited<ReturnType<typeof api.voicePacks>>): string | null {
  const pack =
    state.packs.find((one) => one.id === state.pack) ??
    state.packs.find((one) => one.voices.some((voice) => voice.id === state.voice))
  const engine = pack?.name ?? state.provider
  if (!engine || state.provider === 'silent') return null
  const voice = state.voice
    ? (pack?.voices.find((one) => one.id === state.voice)?.name ?? state.voice)
    : '系统默认音色'
  return `${engine}｜${voice}`
}

function VoiceSettings({ busy }: { busy: boolean }) {
  const [state, setState] = useState<Awaited<ReturnType<typeof api.voicePacks>> | null>(null)
  const [installing, setInstalling] = useState('')
  const [choosing, setChoosing] = useState(false)
  const [failure, setFailure] = useState('')

  const load = () => {
    void api
      .voicePacks()
      .then(setState)
      .catch((error: Error) => setFailure(error.message))
  }
  useEffect(load, [])

  /**
   * Pick a voice, and hear it.
   *
   * One press rather than two controls per chip: the two things someone wants
   * from a list of voices are "what does that one sound like" and "use that
   * one", and doing both makes auditioning the way you choose. It is cheap to
   * undo — the choice only applies to videos made after it, and pressing the
   * one in use hands the choice back to the machine.
   */
  const choose = async (voice: string) => {
    setFailure('')
    setChoosing(true)
    try {
      setState(await api.chooseVoice(voice))
      if (voice) await play(voice)
    } catch (error) {
      setFailure(api.describeError(error))
    } finally {
      setChoosing(false)
    }
  }

  /**
   * Say the sample sentence.
   *
   * `play()` rejects when the element cannot load the source, and that
   * rejection is the only place a failure shows — a silent audio element is
   * indistinguishable from a voice that has not started yet — so it is
   * reported rather than swallowed.
   */
  const play = async (voice: string) => {
    try {
      await new Audio(api.previewVoiceUrl(voice)).play()
    } catch (error) {
      setFailure(`试听放不出来：${api.describeError(error)}`)
    }
  }

  const install = async (pack: api.VoicePack) => {
    setFailure('')
    setInstalling(pack.id)
    try {
      await api.installVoicePack(pack.id)
      load()
    } catch (error) {
      setFailure(api.describeError(error))
    } finally {
      setInstalling('')
    }
  }

  return (
    <>
      <h2 className="modal__title">语音</h2>
      <p className="muted">
        点一个音色，会念一句给你听，同时设成默认，之后的视频都用它；再点一下取消，交回给这台机器自己定。
        单个视频想换，说一句就行：「用播音腔讲」「换个女声」「语速慢一点」。
      </p>

      {/* 「现在用的是哪个」 has to be answerable without making a video. The
          configured value is usually empty and means 「这台机器自己定」, which
          is true but unreadable — so what it resolves to is shown instead, and
          a video that was told otherwise says so on its own page. */}
      {state && (
        <ul className="providers">
          <Line label="当前">
            {inUse(state) ?? '无（本平台暂无可用语音）'}
            {!state.current && <span className="muted">　（这台机器自己定的）</span>}
            {inUse(state) && (
              <Button
                size="small"
                type="text"
                disabled={choosing}
                style={{ marginLeft: 8 }}
                onClick={() => void play(state.voice)}
              >
                试听
              </Button>
            )}
          </Line>
        </ul>
      )}

      {failure && <p className="modal__error">{failure}</p>}
      {state === null && <p className="muted">正在看这台机器有哪些声音…</p>}

      {state?.packs.map((pack) => (
        <div key={pack.id} className="pack">
          <div className="pack__head">
            <span className="pack__name">{pack.name}</span>
            {pack.online && <span className="pack__tag">需联网</span>}
            {pack.installed ? (
              <span className="pack__tag pack__tag--on">已装</span>
            ) : (
              <Button
                size="small"
                loading={installing === pack.id}
                disabled={busy || Boolean(installing)}
                onClick={() => void install(pack)}
              >
                {`安装 ${Math.max(1, Math.round(pack.size / 1024 / 1024))}MB`}
              </Button>
            )}
          </div>
          <div className="muted pack__note">{pack.note}</div>
          {/* Where more voices come from, which is a different answer for each
              of these: one is a service, one brings its own eight, one is the
              operating system's, and one is a file you drop in a folder. */}
          {pack.how && <div className="muted pack__note">{pack.how}</div>}
          {pack.folder && <div className="pack__folder">{pack.folder}</div>}
          {/* 174 published voices is a thing you look through rather than a
              list you read, so it is searchable and it downloads in place. */}
          {pack.id === 'piper' && <PiperBrowser onInstalled={load} />}
          {pack.voices.length > 0 && (
            <div className="pack__voices">
              {pack.voices.map((voice) => (
                <button
                  key={voice.id}
                  type="button"
                  // Installed packs only: choosing a voice out of a pack that
                  // is not here would be accepted and then silently ignored at
                  // synthesis time, which is the worst of both.
                  disabled={!pack.installed || choosing}
                  className={
                    voice.id === state.voice ? 'pack__voice pack__voice--on' : 'pack__voice'
                  }
                  onClick={() => void choose(voice.id === state.voice ? '' : voice.id)}
                >
                  {voice.name}
                  {voice.gender === 'female' && ' 女'}
                  {voice.gender === 'male' && ' 男'}
                  {voice.id === state.voice && ' · 在用'}
                </button>
              ))}
            </div>
          )}
        </div>
      ))}
    </>
  )
}
