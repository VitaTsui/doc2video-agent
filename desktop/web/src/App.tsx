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

// A provider is configured and could not be reached. 「还没配模型」 is the wrong
// thing to say here — it sends the person to a settings panel that already has
// what they need in it, and hides the one sentence that would fix this.
const GREETING_MODEL_UNREACHABLE = (why: string) =>
  '把 PPT 或 PDF 拖进来，再说一句你想要什么样的视频。\n\n' +
  `配了模型但这次没连上：${why}\n` +
  '现在讲稿要你自己写——解析完我会把每页的字数预算列出来。设置里改完会重启后端，改完就能用。'

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
  /** The model writing the script, and how far it has got. Null when it isn't. */
  const [redoing, setRedoing] = useState<number | null>(null)
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
            ? `换成 ${caps.llm.model} 了。下次投文档时我顺手把讲稿写好，你在上面改。`
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
        // The deck card, back in the conversation it was said in. Only the
        // panel was being restored, so a reopened project had its pages but
        // no gate — nothing to press to write the script or start the render,
        // and a visibly shorter conversation than the one that made it.
        const seconds = Math.round(guide.reduce((sum, row) => sum + row.page_seconds, 0))
        say({
          role: 'assistant',
          kind: 'deck',
          // Reopened: whoever set this length did so in a conversation that is
          // being restored, not in one being had — so state it, don't credit it.
          text: `《${summary.title || summary.source}》共 ${deckPages.length} 页，`
            + `目标时长大约 ${seconds} 秒。`,
          projectId: summary.project_id,
          pages: deckPages,
          guide,
          hasModel,
        })
        setArtifacts((current) =>
          current?.projectId === summary.project_id
            ? { ...current, deck: { pages: deckPages, guide, hasModel, locked: false } }
            : current,
        )
        // The script it already has, back in the boxes it belongs to. It was
        // drafted when this deck was parsed; coming back to a project should
        // not mean coming back to blank pages.
        setDrafts(
          Object.fromEntries(
            deckPages
              .filter((page) => page.narration)
              .map((page) => [String(page.index), page.narration]),
          ),
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
   * The asking happens at the button, in the window's own dialog — this runs
   * only once someone has said yes. `window.confirm` used to do it here, and
   * that dialog belongs to the webview: it is the browser's, styled by the
   * platform, and on some of them it does not open at all, which turns a
   * confirmed delete into an unconfirmed one.
   */
  const removeProject = useCallback(
    async (project: ProjectSummary) => {
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
      caps?.llm.available
        ? GREETING_WITH_MODEL(caps.llm.label || caps.llm.provider)
        : caps?.llm.reason
          ? GREETING_MODEL_UNREACHABLE(caps.llm.reason)
          : GREETING_WITHOUT_MODEL,
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

  /**
   * Follow one job, keeping its message updated, then show what came out.
   *
   * `expectsVideo: false` for a turn whose product is not a film — writing the
   * script is the one. It ended with 「这一轮没有出片。」, which is true and reads
   * as a complaint about a step that did exactly what it was asked to do.
   */
  const follow = useCallback(
    async (jobId: string, intro: string, expectsVideo = true) => {
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

      // Stopping on purpose is not a failure, and the card above already
      // says 「已中止」 — a second line calling it one would be wrong twice.
      if (final.status === 'cancelled') return

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
      if (rendered) {
        say({
          role: 'assistant',
          kind: 'video',
          text: final.reply || '好了。',
          projectId: final.project_id,
          scenes,
          quality,
          ledger: chain,
        })
        return
      }
      // A turn that was never going to end in a film says what it did produce
      // where it produced it — the caller knows the count. Nothing to add here
      // but a second sentence saying the same thing more vaguely.
      const closing = final.reply || (expectsVideo ? '这一轮没有出片。' : '')
      if (closing) say({ role: 'assistant', kind: 'text', text: closing })
    },
    [amend, projectId, say],
  )

  /** Put the run record into the panel, if it still belongs to this project. */
  const showRecord = useCallback((entries: api.LedgerEntry[]) => {
    setArtifacts((current) => (current ? { ...current, ledger: entries } : current))
  }, [])

  /**
   * Step two: write the script for a deck that has just been parsed.
   *
   * Separate from the parse because the two take very different amounts of
   * time. The deck is on screen in seconds; the words take as long as the
   * model takes, and they land page by page — every batch the model finishes
   * is saved, and this reads them back into the boxes as they appear, so the
   * wait is spent watching the script get written rather than watching a
   * spinner.
   *
   * Asked for rather than automatic. The deck is on screen with empty boxes
   * first, so anyone who already knows what page 4 should say can type it and
   * have the rest written around it — the model gets those pages as context
   * and leaves them alone. Starting to write the moment the parse landed took
   * that away: by the time the boxes appeared they were already full, and
   * writing your own page meant overwriting someone else's.
   */
  const draftScript = useCallback(
    async (project: string, written: Record<string, string>) => {
      const already = Object.entries(written).filter(([, text]) => text.trim())

      // The pages fill in as they are written — every batch the model
      // finishes is saved, so the boxes on the right can be read while the
      // rest is still being written.
      const fill = (pages: api.PageView[]) => {
        const filled = pages.filter((page) => page.narration)
        if (filled.length === 0) return 0
        setDrafts(Object.fromEntries(filled.map((p) => [String(p.index), p.narration])))
        return filled.length
      }

      const jobId = await api.draftScript(project, Object.fromEntries(already))
      const poll = window.setInterval(() => {
        void api.pages(project).then(fill).catch(() => 0)
        // The record grows through this step as much as through a render —
        // this is where the model is deciding things — so it is read here too.
        void api.ledger(project).then(showRecord).catch(() => undefined)
      }, 1500)
      try {
        // The same card a render reports through, in the same place: a line
        // of its own below, with what it is doing and a way to stop it. Two
        // presentations of one wait is one more than the wait deserves.
        await follow(jobId, '开始写讲稿，逐页来。', false)
        return fill(await api.pages(project).catch(() => []))
      } finally {
        window.clearInterval(poll)
        void api.ledger(project).then(showRecord).catch(() => undefined)
      }
    },
    [follow, showRecord],
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
          // 「按这个要求」 only when there was one. A brief that never mentions
          // a length gets the default, and reporting the default as though it
          // were the request is how a video comes back a different length than
          // the person thought they had asked for.
          text: prepared.duration_stated
            ? `《${prepared.title}》共 ${prepared.pages.length} 页，按这个要求算下来大约 ${seconds} 秒。`
            : `《${prepared.title}》共 ${prepared.pages.length} 页。没说要多长，先按默认算下来大约 ${seconds} 秒——想改直接说，比如「七分钟左右」。`,
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
        // Parsing and reading the deck are decisions too, and they were
        // already recorded by the time this returned. Without this read the
        // 过程 tab stayed empty until a render, which made it look like
        // nothing had happened yet.
        void api.ledger(prepared.project_id).then(showRecord).catch(() => undefined)

        // The words are the next step, and it is the user's to take: the
        // boxes are empty and open, and 「生成讲稿」 fills in whatever is
        // still blank when they press it.
      } catch (error) {
        amend(thinking, { pending: false, kind: 'text', text: `解析失败：${api.describeError(error)}` })
      } finally {
        setBusy(false)
      }
    },
    [amend, hasModel, loadProjects, say, showRecord],
  )

  /**
   * 「重新生成这一页」: redo one page, keeping the rest of the film.
   *
   * The alternative is 开始生成, which costs minutes: on a 30-page deck the
   * first render took 263 seconds and redoing one page took 18, because every
   * scene whose plan still hashes the same keeps the clip it already has. That
   * saving existed in the pipeline and had no way to be asked for from here —
   * editing a box after a render left nothing to press but 重新生成 (the whole
   * film).
   */
  const redoPage = useCallback(
    async (page: number) => {
      const project = projectId
      const scene = artifacts?.scenes.find((one) => one.source_page === page)
      const text = (drafts[page] ?? '').trim()
      if (!project || !scene || !text) return
      setRedoing(page)
      try {
        const { job_id } = await api.reviseScene(project, scene.scene_id, text)
        await follow(job_id, `重做第 ${page} 页，其余片段不动。`)
      } catch (error) {
        say({ role: 'assistant', kind: 'text', text: `没能重做：${api.describeError(error)}` })
      } finally {
        setRedoing(null)
      }
    },
    [artifacts, drafts, follow, projectId, say],
  )

  /**
   * 「生成讲稿」: fill in the pages nobody has written.
   *
   * What is in the boxes goes with it and comes back untouched. The model
   * writes only the gaps, and it is shown the written pages on either side —
   * a page written blind to its neighbours opens by introducing something the
   * page before it just finished explaining.
   */
  const fillInScript = useCallback(async () => {
    const project = projectId
    const deck = artifacts?.deck
    // One job at a time: the gate stays on screen while the script is being
    // written, and a second press would queue a second run over the first.
    if (!project || !deck || running) return
    // Nothing left blank means this is a rewrite, and a rewrite keeps nothing:
    // sending the current text back would be asking the model to fill in no
    // gaps at all, which is a button that does nothing.
    const blank = deck.pages.some((page) => !(drafts[page.index] ?? '').trim())
    const keep = blank ? drafts : {}
    if (!blank) setDrafts({})
    try {
      const written = await draftScript(project, keep)
      if (written === 0) {
        say({
          role: 'assistant',
          kind: 'text',
          text: '讲稿没写出来，可以自己写，或者留空让占位文本顶上。',
        })
      } else {
        say({
          role: 'assistant',
          kind: 'text',
          text: `讲稿写好了，${written} 页都在右侧，逐页可以改。改完点「开始生成」。`,
        })
      }
    } catch (error) {
      say({ role: 'assistant', kind: 'text', text: `写讲稿失败：${api.describeError(error)}` })
    }
  }, [artifacts, draftScript, drafts, projectId, running, say])

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
              // Which deck all of this is about. A conversation can hold more
              // than one document card, and every field below describes the
              // one being worked on — without this they describe all of them,
              // and a second upload leaves two cards claiming to be writing.
              projectId,
              pages: artifacts?.deck?.pages.length ?? 0,
              hasModel,
              written: Object.values(drafts).filter((text) => text.trim()).length,
              locked: Boolean(artifacts?.deck?.locked),
              // Greyed while anything is running: the card no longer swaps
              // itself for a progress line, so both buttons stay on screen
              // through a run that is already under way. Separate from
              // `locked`, which is what makes the button read 「已开始」 —
              // and 「已开始」 is a lie while it is the script being written.
              busy: running,
              // Whether a film exists — which is what 「重新生成」 claims and
              // 「开始生成」 denies. Scene count is not that: writing the script
              // creates a scene per page, so the button flipped to 「重新生成」
              // the moment the script was written, offering to redo a video
              // that had never been made.
              generated: Boolean(artifacts?.rendered),
              onRender: () => void startRender(),
              onDraft: () => void fillInScript(),
            }}
            onStop={(jobId) => {
              // A request, not a kill: the scene in flight finishes, and the
              // card says 「正在停…」 until it does.
              void api.cancelJob(jobId).catch((error) => {
                say({ role: 'assistant', kind: 'text', text: `没能中止：${api.describeError(error)}` })
              })
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
        onRedo={(page) => void redoPage(page)}
        redoing={redoing}
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
              ? `模型已就绪：${caps.llm.label || caps.llm.provider}。投文档时我就把讲稿写好，你在上面改。`
              : '设置已保存，后端已重启。',
          })
        }}
      />
    </div>
  )
}
