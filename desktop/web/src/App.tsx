/**
 * The whole product, as a conversation.
 *
 * The pipeline's order still governs — a script cannot be written before the
 * deck is parsed, and cannot be changed after voicing without re-synthesising —
 * but the user is not walked through it as numbered steps. They drop a deck and
 * say what they want; each reply is whatever that turn produced: a summary of
 * the deck, a live progress line, a finished video.
 *
 * One gate stays visible, and it earns its place: between "parsed" and
 * "generating" there is a confirmation, because everything before it takes
 * seconds and everything after it takes minutes.
 */

import { useCallback, useEffect, useRef, useState } from 'react'

import * as api from './api'
import type { Connection, JobState } from './api'
import { Composer } from './chat/Composer'
import type { ModelGroup } from './chat/Composer'
import { MessageList } from './chat/MessageList'
import type { Message, MessageDraft, MessagePatch } from './chat/types'
import { SettingsDrawer } from './SettingsDrawer'
import { Setup } from './Setup'

let counter = 0
const nextId = () => `m${++counter}`

const GREETING_WITH_MODEL = (provider: string) =>
  `把 PPT 或 PDF 拖进来，再说一句你想要什么样的视频——讲稿我来写（${provider}）。\n\n出片之后想改哪一页直接说，比如「第 3 页太长了，压到 20 秒」。`

const GREETING_WITHOUT_MODEL =
  '把 PPT 或 PDF 拖进来，再说一句你想要什么样的视频。\n\n' +
  '还没配模型，所以讲稿要你自己写——解析完我会把每页的字数预算列出来。想让我代写的话，' +
  '在设置里配一个 API Key；本机装了 Claude Code 或 Codex 的话，不用 Key 也能直接用它们。'

export function App() {
  const [connection, setConnection] = useState<Connection | null>(null)
  const [failure, setFailure] = useState<string | null>(null)
  const [runtime, setRuntime] = useState<api.RuntimeStatus | null>(null)
  const [messages, setMessages] = useState<Message[]>([])
  const [projectId, setProjectId] = useState<string | null>(null)
  const [busy, setBusy] = useState(false)
  const [hasModel, setHasModel] = useState(false)
  const [settingsOpen, setSettingsOpen] = useState(false)
  const [groups, setGroups] = useState<ModelGroup[]>([])
  const [model, setModel] = useState('')

  const abort = useRef<AbortController | null>(null)
  // StrictMode runs effects twice in development; without this the opening
  // message is said twice, which is also what a retry would look like.
  const greeted = useRef(false)

  const say = useCallback((message: MessageDraft) => {
    const id = nextId()
    setMessages((prev) => [...prev, { ...message, id } as Message])
    return id
  }, [])

  const amend = useCallback((id: string, patch: MessagePatch) => {
    setMessages((prev) => prev.map((m) => (m.id === id ? ({ ...m, ...patch } as Message) : m)))
  }, [])

  /** Everything the picker offers: no model, then each provider's entries. */
  const loadModels = useCallback(async () => {
    const [catalogue, prefs] = await Promise.all([api.catalogue(), api.modelPrefs()])
    // Two groups, because that is the choice being made: a CLI already on this
    // machine costs nothing per run, an API key does. Within a group the
    // model's own name carries the vendor — "Claude Opus 5" says whose it is.
    const pick = (needsKey: boolean) =>
      catalogue.providers
        .filter((provider) => provider.needs_key === needsKey)
        .flatMap((provider) =>
          (catalogue.models[provider.id] ?? []).map((entry) => ({
            value: `${provider.id}/${entry.id}`,
            // The vendor is already in the model's name — "Claude Opus 5",
            // "GPT-5", "Gemini 2.5 Pro" — and the group heading carries what
            // actually differs. Prefixing the provider only truncates.
            label: entry.label,
          })),
        )

    setGroups([
      { label: '不用模型', models: [{ value: '', label: '讲稿我自己写' }] },
      { label: '本机 CLI · 不要 Key', models: pick(false) },
      { label: '需要 API Key', models: pick(true) },
    ])
    setModel(prefs.provider ? `${prefs.provider}/${prefs.model}` : '')
  }, [])

  /** Switching model restarts the backend — its settings are frozen per process. */
  const switchModel = useCallback(
    async (value: string) => {
      const [provider, model_id = ''] = value ? value.split('/') : ['']
      setModel(value)
      setBusy(true)
      try {
        const next = await api.saveModelPrefs({
          provider,
          model: model_id,
          base_url: '',
        })
        setConnection(next)
        const caps = await api.capabilities().catch(() => null)
        setHasModel(Boolean(caps?.llm.available))
        say({
          role: 'assistant',
          kind: 'text',
          text: caps?.llm.available
            ? `换成 ${caps.llm.provider}｜${caps.llm.model} 了，之后留空的页我来写。`
            : '好，讲稿由你来写。留空的页会是占位文本。',
        })
      } catch (error) {
        say({ role: 'assistant', kind: 'text', text: `切换失败：${api.describeError(error)}` })
      } finally {
        setBusy(false)
      }
    },
    [say],
  )

  /**
   * Put the last conversation back on screen, if there was one.
   *
   * Only the replies are replayed. Each turn of the loop also records the
   * reason behind its decision and what the tools did, and those already have
   * a home — the ledger under the video, where they sit next to the render
   * they caused. Repeating them here would be the same story told twice.
   */
  const resume = useCallback(async () => {
    const [latest] = await api.projects()
    if (!latest) return false
    const past = await api.session(latest.project_id)
    if (past.items.length === 0) return false

    setProjectId(latest.project_id)
    const spoken: MessageDraft[] = []
    for (const turn of past.items) {
      if (turn.speaker === 'summary') {
        // Say so rather than quietly showing a shorter history: those turns
        // are gone for the agent too, and a user quoting them would be
        // quoting something it can no longer see.
        spoken.push({ role: 'assistant', kind: 'text', text: `（更早的对话已折叠）${turn.text}` })
      } else if (turn.speaker === 'user') {
        spoken.push({ role: 'user', kind: 'text', text: turn.text })
      } else if (turn.speaker === 'agent' && !turn.action) {
        spoken.push({ role: 'assistant', kind: 'text', text: turn.text })
      }
    }
    spoken.forEach(say)

    if (latest.output) {
      const [scenes, quality, chain] = await Promise.all([
        api.scenes(latest.project_id),
        api.quality(latest.project_id).catch(() => null),
        api.ledger(latest.project_id).catch(() => []),
      ])
      say({
        role: 'assistant',
        kind: 'video',
        text: `上次做到这里：《${latest.title || latest.source}》，${Math.round(latest.duration)} 秒。`,
        projectId: latest.project_id,
        scenes,
        quality,
        ledger: chain,
      })
    }
    return true
  }, [say])

  /** Connect, learn what the backend can do, and open the conversation. */
  const begin = useCallback(async () => {
    const next = await api.connect()
    setConnection(next)
    const caps = await api.capabilities().catch(() => null)
    setHasModel(Boolean(caps?.llm.available))
    void loadModels()

    // A returning user gets their conversation back instead of a greeting they
    // have already read.
    if (await resume().catch(() => false)) return

    say({
      role: 'assistant',
      kind: 'text',
      text: caps?.llm.available ? GREETING_WITH_MODEL(caps.llm.provider) : GREETING_WITHOUT_MODEL,
    })
  }, [loadModels, resume, say])

  useEffect(() => {
    if (greeted.current) return
    greeted.current = true
    // Nothing can start before the runtime is there, so that question comes
    // first — and its answer decides whether this is a chat or a download.
    api
      .runtimeStatus()
      .then((status) => {
        setRuntime(status)
        return status.ready ? begin() : undefined
      })
      .catch((error: Error) => setFailure(error.message))
  }, [begin])

  /** Follow one job, keeping its message updated, then show what came out. */
  const follow = useCallback(
    async (jobId: string, intro: string) => {
      const id = say({ role: 'assistant', kind: 'job', text: intro, job: null })
      abort.current?.abort()
      abort.current = new AbortController()

      let final: JobState
      try {
        final = await api.watchJob(jobId, (state) => amend(id, { job: state }), abort.current.signal)
      } catch (error) {
        amend(id, { kind: 'text', text: `没能跟上进度：${api.describeError(error)}` })
        return
      }
      amend(id, { job: final })

      if (final.status !== 'succeeded' || !final.project_id) {
        say({
          role: 'assistant',
          kind: 'text',
          text: `生成失败了：${final.error?.message ?? final.detail}`,
        })
        return
      }

      const [scenes, quality, chain] = await Promise.all([
        api.scenes(final.project_id),
        api.quality(final.project_id).catch(() => null),
        api.ledger(final.project_id).catch(() => []),
      ])

      // A turn can end without a video — the agent asked something, or stopped
      // before rendering. Showing a player pointed at a file that does not
      // exist would be worse than saying only what happened.
      const rendered = Boolean(final.result?.output_path)
      say(
        rendered
          ? {
              role: 'assistant',
              kind: 'video',
              text: final.reply || '好了。',
              projectId: final.project_id,
              scenes,
              quality,
              ledger: chain,
            }
          : { role: 'assistant', kind: 'text', text: final.reply || '这一轮没有出片。' },
      )
    },
    [amend, say],
  )

  /** A deck arrives: parse it, then report what is in it and what it will cost. */
  const acceptDeck = useCallback(
    async (file: File, brief: string) => {
      setBusy(true)
      say({ role: 'user', kind: 'text', text: brief || '按默认来', file: file.name })
      const thinking = say({ role: 'assistant', kind: 'text', text: '正在解析…' })
      try {
        const uploadId = await api.uploadSource(file)
        const prepared = await api.prepare(uploadId, brief)
        const guide = await api.narrationGuide(prepared.project_id)
        setProjectId(prepared.project_id)
        const seconds = Math.round(guide.reduce((sum, row) => sum + row.page_seconds, 0))
        amend(thinking, {
          kind: 'deck',
          text: `《${prepared.title}》共 ${prepared.pages.length} 页，按这个要求算下来大约 ${seconds} 秒。`,
          projectId: prepared.project_id,
          pages: prepared.pages,
          guide,
          hasModel,
        })
      } catch (error) {
        amend(thinking, { kind: 'text', text: `解析失败：${api.describeError(error)}` })
      } finally {
        setBusy(false)
      }
    },
    [amend, hasModel, say],
  )

  /** A plain message: either a follow-up edit, or a nudge to drop a deck. */
  const acceptMessage = useCallback(
    async (text: string) => {
      say({ role: 'user', kind: 'text', text })
      if (!projectId) {
        say({
          role: 'assistant',
          kind: 'text',
          text: '还没有文档。点左下角的回形针挑一份 PPT 或 PDF，我才有东西可讲。',
        })
        return
      }
      setBusy(true)
      try {
        const { job_id } = await api.chat(projectId, text)
        await follow(job_id, '我看看现在这一版，想想该改什么。')
      } catch (error) {
        say({ role: 'assistant', kind: 'text', text: `没能开始：${api.describeError(error)}` })
      } finally {
        setBusy(false)
      }
    },
    [follow, projectId, say],
  )

  const startRender = useCallback(
    async (id: string, narrations: Record<string, string>) => {
      if (!projectId) return
      setBusy(true)
      amend(id, { locked: true })
      try {
        // Written pages are an instruction, so they go straight down the
        // pipeline. An empty form with a model configured is the opposite —
        // it means "you decide" — and that belongs in the loop, where the
        // agent can read its own quality report afterwards and fix a page.
        const written = Object.values(narrations).some((text) => text.trim())
        const { job_id } =
          !written && hasModel
            ? await api.chat(projectId, '按这份文档生成视频。')
            : await api.submitNarrations(projectId, narrations)
        await follow(job_id, '开始了，渲染要几分钟。')
      } catch (error) {
        amend(id, { locked: false })
        say({ role: 'assistant', kind: 'text', text: `没能开始：${api.describeError(error)}` })
      } finally {
        setBusy(false)
      }
    },
    [amend, follow, hasModel, projectId, say],
  )

  if (runtime && !runtime.ready) {
    return (
      <Setup
        status={runtime}
        onReady={() => {
          setRuntime({ ...runtime, ready: true })
          void begin()
        }}
      />
    )
  }

  if (failure) {
    return (
      <div className="shell">
        <div className="transcript">
          <div className="column">
            <h3>后端没能启动</h3>
            <p className="muted">界面本身没问题，是它背后的处理进程起不来。下面是它退出前的输出：</p>
            <pre style={{ whiteSpace: 'pre-wrap', fontSize: 12 }}>{failure}</pre>
          </div>
        </div>
      </div>
    )
  }

  return (
    <div className="shell">
      <header className="topbar">
        <span className="topbar__name">Doc2Video</span>
        <button type="button" className="topbar__button" onClick={() => setSettingsOpen(true)}>
          设置
        </button>
      </header>

      <MessageList messages={messages} onRender={startRender} />

      <Composer
        disabled={!connection || busy}
        onSend={acceptMessage}
        onDeck={acceptDeck}
        hint={projectId ? '想改哪里就直接说' : '说说你想要什么样的视频，并附上文档'}
        groups={groups}
        model={model}
        onModel={(value) => void switchModel(value)}
      />

      <SettingsDrawer
        open={settingsOpen}
        onClose={() => setSettingsOpen(false)}
        onReconnected={async (next) => {
          setConnection(next)
          const caps = await api.capabilities().catch(() => null)
          setHasModel(Boolean(caps?.llm.available))
          say({
            role: 'assistant',
            kind: 'text',
            text: caps?.llm.available
              ? `模型已就绪：${caps.llm.provider}｜${caps.llm.model}。之后留空的页我来写。`
              : '设置已保存，后端已重启。',
          })
        }}
      />
    </div>
  )
}
