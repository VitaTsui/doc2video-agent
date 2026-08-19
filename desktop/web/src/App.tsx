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
import type { Connection, JobState, ProjectSummary } from './api'
import { Composer } from './chat/Composer'
import { MessageList } from './chat/MessageList'
import type { Message, MessageDraft, MessagePatch } from './chat/types'
import { Settings } from './Settings'
import { FileIcon } from './Icon'
import { Sidebar } from './Sidebar'
import { Artifacts } from './Artifacts'
import type { ArtifactSet } from './Artifacts'
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
  const [projects, setProjects] = useState<ProjectSummary[]>([])
  const [collapsed, setCollapsed] = useState(false)
  const [artifacts, setArtifacts] = useState<ArtifactSet | null>(null)
  const [panelOpen, setPanelOpen] = useState(false)
  // The script being typed: written in the panel, committed from the
  // conversation, so neither of them can own it.
  const [drafts, setDrafts] = useState<Record<string, string>>({})
  const [running, setRunning] = useState(false)
  const [greeting, setGreeting] = useState('')
  const [notice, setNotice] = useState('')
  const [busy, setBusy] = useState(false)
  const [hasModel, setHasModel] = useState(false)
  const [settingsOpen, setSettingsOpen] = useState(false)
  const [prefs, setPrefs] = useState<api.ModelPrefs>({
    providers: [],
    active: '',
    active_model: '',
  })

  const abort = useRef<AbortController | null>(null)
  const [dragging, setDragging] = useState(false)
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

  /** The configured providers, which is everything the picker shows. */
  const loadModels = useCallback(async () => {
    setPrefs(await api.modelPrefs())
  }, [])

  /** Switching model restarts the backend — its settings are frozen per process. */
  const switchModel = useCallback(
    async (providerId: string, modelId: string) => {
      setBusy(true)
      try {
        const current = await api.modelPrefs()
        const next = { ...current, active: providerId, active_model: modelId }
        setPrefs(next)
        setConnection(await api.saveModelPrefs(next))
        const caps = await api.capabilities().catch(() => null)
        setHasModel(Boolean(caps?.llm.available))
        say({
          role: 'assistant',
          kind: 'text',
          text: caps?.llm.available
            ? `换成 ${caps.llm.model} 了，之后留空的页我来写。`
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

  /** Collect a project's outputs for the side panel. */
  const loadArtifacts = useCallback(async (projectId: string, rendered: boolean) => {
    const [scenes, quality, chain] = await Promise.all([
      api.scenes(projectId).catch(() => []),
      api.quality(projectId).catch(() => null),
      api.ledger(projectId).catch(() => []),
    ])
    // The deck is not re-fetched here; it belongs to the parse that produced
    // it, and dropping it would empty the tab someone is looking at.
    let carried: ArtifactSet['deck']
    setArtifacts((current) => {
      carried = current?.projectId === projectId ? current.deck : undefined
      return { projectId, scenes, quality, ledger: chain, rendered, deck: carried }
    })
    return { projectId, scenes, quality, ledger: chain, rendered, deck: carried }
  }, [])

  /**
   * Put one project's conversation on screen, replacing whatever is there.
   *
   * Only the replies are replayed. Each turn of the loop also records the
   * reason behind its decision and what the tools did, and those already have
   * a home — the ledger under the video, where they sit next to the render
   * they caused. Repeating them here would be the same story told twice.
   */
  const openProject = useCallback(
    async (summary: ProjectSummary, greeting?: string) => {
      setProjectId(summary.project_id)
      setMessages([])

      const past = await api.session(summary.project_id).catch(() => ({ items: [], compacted: 0 }))
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
      if (spoken.length === 0 && greeting) {
        spoken.push({ role: 'assistant', kind: 'text', text: greeting })
      }
      spoken.forEach(say)

      // Its pages too, so the script has somewhere to be read and changed.
      // Without this, reopening a project gave a panel with no 文档 tab and a
      // script that existed only as read-only lines under 逐页.
      const [deckPages, guide] = await Promise.all([
        api.pages(summary.project_id).catch(() => []),
        api.narrationGuide(summary.project_id).catch(() => []),
      ])
      const set = await loadArtifacts(summary.project_id, Boolean(summary.output))
      if (deckPages.length > 0) {
        setArtifacts((current) =>
          current?.projectId === summary.project_id
            ? { ...current, deck: { pages: deckPages, guide, hasModel, locked: false } }
            : current,
        )
        // Open on anything worth seeing, not only on a finished video: a
        // project that was parsed and never rendered is exactly the one whose
        // pages someone came back to look at.
        setPanelOpen(true)
      }
      if (summary.output) {
        say({
          role: 'assistant',
          kind: 'video',
          text: `《${summary.title || summary.source}》，${Math.round(summary.duration)} 秒。`,
          projectId: summary.project_id,
          scenes: set.scenes,
          quality: set.quality,
          ledger: set.ledger,
        })
        setPanelOpen(true)
      }
    },
    [hasModel, loadArtifacts, say],
  )

  /** Everything on this machine, newest first — the sidebar's whole content. */
  const loadProjects = useCallback(async () => {
    const items = await api.projects().catch(() => [])
    setProjects(items)
    return items
  }, [])

  /**
   * Delete a project and everything it produced.
   *
   * Confirmed first, and the sentence says what survives: the uploaded file
   * stays where it was, so this costs the video and not the deck.
   */
  const removeProject = useCallback(
    async (project: ProjectSummary) => {
      const name = project.title || project.source || '这个工程'
      if (!window.confirm(`删除《${name}》及其生成的全部内容？\n\n上传的原文件不会被删除。`)) {
        return
      }
      try {
        await api.deleteProject(project.project_id)
      } catch (error) {
        say({ role: 'assistant', kind: 'text', text: `没能删除：${api.describeError(error)}` })
        return
      }
      const items = await loadProjects()
      // Looking at the one that just went: back to the opening screen rather
      // than at a transcript for something that no longer exists.
      if (projectId === project.project_id) {
        setProjectId(null)
        setMessages([])
        setArtifacts(null)
        setPanelOpen(false)
        setDrafts({})
      }
      return items
    },
    [loadProjects, projectId, say],
  )

  /** Start over: no project, an empty transcript, back to the opening screen. */
  const startNew = useCallback(() => {
    setProjectId(null)
    setMessages([])
    setArtifacts(null)
    setPanelOpen(false)
    setDrafts({})
    greeted.current = false
  }, [])

  /** Connect, learn what the backend can do, and open the conversation. */
  const begin = useCallback(async () => {
    const next = await api.connect()
    setConnection(next)
    const caps = await api.capabilities().catch(() => null)
    setHasModel(Boolean(caps?.llm.available))
    void loadModels()

    // Silent: a check that pops a dialog before the user has done anything is
    // an interruption, and installing costs a restart. A line on the opening
    // screen, where it waits until they care.
    void api.checkUpdate().then((update) => {
      if (update?.available) {
        setNotice(`有新版本 ${update.version}（当前 ${update.current}），在设置里可以更新。`)
      }
    })

    // The list, not one of its entries. Reopening the last project on launch
    // made sense when the sidebar did not exist and there was no other way
    // back to it; now it just means the app opens onto something you were
    // finished with, and starting the next one takes a click to undo.
    void loadProjects()

    // Shown on the opening screen rather than said as a turn: a greeting that
    // is a message means the transcript is never empty, so the centred opening
    // screen — the one 「新会话」 gives — could never appear on launch.
    setGreeting(
      caps?.llm.available ? GREETING_WITH_MODEL(caps.llm.provider) : GREETING_WITHOUT_MODEL,
    )
  }, [loadModels, loadProjects])

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

      // Everything here is written step by step, so it can be read step by
      // step: the executor saves the project after every stage and the record
      // is appended as each one ends. Waiting for the finish turned the views
      // that explain a slow run into things you could only consult once the
      // run was over.
      setRunning(true)
      setPanelOpen(true)
      // Which project to read while it works — taken from the job rather than
      // from state, because the first run of all creates its project inside
      // the job and nothing out here knows the id until it reports one.
      const watching = { current: projectId }
      const poll = window.setInterval(() => {
        const id = watching.current
        if (!id) return
        void Promise.all([
          api.ledger(id).catch(() => null),
          api.scenes(id).catch(() => null),
        ]).then(([entries, scenes]) => {
          setArtifacts((current) =>
            current?.projectId === id
              ? {
                  ...current,
                  ledger: entries ?? current.ledger,
                  scenes: scenes ?? current.scenes,
                }
              : current,
          )
        })
      }, 1500)

      let final: JobState
      try {
        final = await api.watchJob(
          jobId,
          (state) => {
            if (state.project_id) watching.current = state.project_id
            amend(id, { job: state })
          },
          abort.current.signal,
        )
      } catch (error) {
        amend(id, { kind: 'text', text: `没能跟上进度：${api.describeError(error)}` })
        return
      } finally {
        window.clearInterval(poll)
        setRunning(false)
      }
      amend(id, { job: final })
      // A finished turn changes a project's duration, its output, and its
      // place in the list; the sidebar is stale until it is re-read.
      void loadProjects()
      // And it releases the gate: without this the button says 「已开始」 for
      // the rest of the session, so a second pass is impossible.
      setArtifacts((current) =>
        current?.deck ? { ...current, deck: { ...current.deck, locked: false } } : current,
      )

      if (final.status !== 'succeeded' || !final.project_id) {
        say({
          role: 'assistant',
          kind: 'text',
          text: `生成失败了：${final.error?.message ?? final.detail}`,
        })
        return
      }

      const rendered = Boolean(final.result?.output_path)
      const { scenes, quality, ledger: chain } = await loadArtifacts(final.project_id, rendered)
      if (rendered) setPanelOpen(true)

      // A turn can end without a video — the agent asked something, or stopped
      // before rendering. Showing a player pointed at a file that does not
      // exist would be worse than saying only what happened.
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
    [amend, projectId, say],
  )

  /** A deck arrives: parse it, then report what is in it and what it will cost. */
  const acceptDeck = useCallback(
    async (file: File, brief: string, uploaded?: string) => {
      setBusy(true)
      say({ role: 'user', kind: 'text', text: brief || '按默认来', file: file.name })
      const thinking = say({ role: 'assistant', kind: 'text', text: '正在解析…', pending: true })
      try {
        // Already on the backend if the picker managed it; only a failed
        // upload has to be repeated here.
        const uploadId = uploaded ?? (await api.uploadSource(file))
        const prepared = await api.prepare(uploadId, brief)
        const guide = await api.narrationGuide(prepared.project_id)
        setProjectId(prepared.project_id)
        void loadProjects()
        const seconds = Math.round(guide.reduce((sum, row) => sum + row.page_seconds, 0))
        amend(thinking, {
          pending: false,
          kind: 'deck',
          text: `《${prepared.title}》共 ${prepared.pages.length} 页，按这个要求算下来大约 ${seconds} 秒。`,
          projectId: prepared.project_id,
          pages: prepared.pages,
          guide,
          hasModel,
        })
        // The deck itself goes to the panel; the sentence above stays here.
        setDrafts({})
        setArtifacts({
          projectId: prepared.project_id,
          scenes: [],
          quality: null,
          ledger: [],
          rendered: false,
          deck: { pages: prepared.pages, guide, hasModel, locked: false },
        })
        setPanelOpen(true)
      } catch (error) {
        amend(thinking, { pending: false, kind: 'text', text: `解析失败：${api.describeError(error)}` })
      } finally {
        setBusy(false)
      }
    },
    [amend, hasModel, loadProjects, say],
  )

  /**
   * A deck dropped onto the window.
   *
   * Tauri intercepts file drops by default and hands the app its own event
   * instead, which means the HTML `drop` never fires and dragging a PPT in
   * does nothing at all — which is what it did. `dragDropEnabled: false` gives
   * the drop back to the page, and this is what catches it.
   */
  const acceptDrop = useCallback(
    (event: React.DragEvent) => {
      event.preventDefault()
      setDragging(false)
      const file = event.dataTransfer.files[0]
      if (!file) return
      if (!/\.(pdf|pptx?)$/i.test(file.name)) {
        say({
          role: 'assistant',
          kind: 'text',
          text: `${file.name} 不是我能读的格式，需要 PDF、PPT 或 PPTX。`,
        })
        return
      }
      void acceptDeck(file, '')
    },
    [acceptDeck, say],
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
    async () => {
      if (!projectId) return
      const narrations = drafts
      setBusy(true)
      // The deck lives in the panel, so locking it is a panel-side fact now.
      setArtifacts((current) =>
        current?.deck ? { ...current, deck: { ...current.deck, locked: true } } : current,
      )
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
        setArtifacts((current) =>
          current?.deck ? { ...current, deck: { ...current.deck, locked: false } } : current,
        )
        say({ role: 'assistant', kind: 'text', text: `没能开始：${api.describeError(error)}` })
      } finally {
        setBusy(false)
      }
    },
    [drafts, follow, hasModel, projectId, say],
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
    <div
      className="layout"
      onDragOver={(event) => {
        event.preventDefault()
        setDragging(true)
      }}
      onDragLeave={(event) => {
        // Only when the pointer leaves the window itself; moving between
        // children fires this constantly and the overlay would flicker.
        if (event.currentTarget.contains(event.relatedTarget as Node | null)) return
        setDragging(false)
      }}
      onDrop={acceptDrop}
    >
      {dragging && (
        <div
          style={{
            position: 'fixed',
            inset: 0,
            zIndex: 20,
            display: 'grid',
            placeItems: 'center',
            background: 'rgb(0 0 0 / 25%)',
            pointerEvents: 'none',
            fontSize: 18,
            color: '#fff',
          }}
        >
          松手就开始解析
        </div>
      )}
      <Sidebar
        projects={projects}
        current={projectId}
        collapsed={collapsed}
        onOpen={(id) => {
          const summary = projects.find((p) => p.project_id === id)
          if (summary) void openProject(summary)
        }}
        onDelete={(project) => void removeProject(project)}
        onNew={startNew}
        onSettings={() => setSettingsOpen(true)}
        onToggle={() => setCollapsed((v) => !v)}
      />

      <main className={messages.length === 0 && !projectId ? 'main main--empty' : 'main'}>
        {/* The way back in. Closing the panel used to be one-way: nothing on
            screen said the deck and the video were still there. */}
        {!panelOpen && artifacts && (
          <button
            type="button"
            className="main__reopen"
            title="打开产物面板"
            onClick={() => setPanelOpen(true)}
          >
            <FileIcon size={19} />
          </button>
        )}
        {/* Before there is a project the composer belongs in the middle of the
            window, not pinned to the bottom of an empty page — an empty
            transcript with an input bar under it reads as something that
            failed to load. */}
        {messages.length === 0 && !projectId ? (
          <div className="empty">
            <h1 className="empty__title">把文档讲成视频</h1>
            <p className="muted empty__hint">
              {greeting || '拖一份 PPT 或 PDF 进来，再说一句你想要什么样的视频。'}
            </p>
            {notice && <p className="muted empty__hint">{notice}</p>}
          </div>
        ) : (
          <MessageList
            messages={messages}
            deck={{
              written: Object.values(drafts).filter((text) => text.trim()).length,
              locked: Boolean(artifacts?.deck?.locked),
              // A script that came out of a render, rather than out of the
              // boxes: the same fields, a different sentence.
              generated: (artifacts?.scenes.length ?? 0) > 0,
              onRender: () => void startRender(),
            }}
            onShow={(id) => {
              void loadArtifacts(id, true)
              setPanelOpen(true)
            }}
          />
        )}

        <Composer
          disabled={!connection || busy}
          uploadAction={connection ? api.uploadUrl() : ''}
          onSend={acceptMessage}
          onDeck={acceptDeck}
          hint={projectId ? '想改哪里就直接说' : '说说你想要什么样的视频，并附上文档'}
          prefs={prefs}
          onPick={(providerId, modelId) => void switchModel(providerId, modelId)}
        />
      </main>

      <Artifacts
        set={artifacts}
        running={running}
        open={panelOpen}
        onClose={() => setPanelOpen(false)}
        drafts={drafts}
        onDrafts={setDrafts}
      />

      <Settings
        open={settingsOpen}
        busy={busy}
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
