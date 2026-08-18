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
import { MessageList } from './chat/MessageList'
import type { Message, MessageDraft, MessagePatch } from './chat/types'
import { SettingsDrawer } from './SettingsDrawer'

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
  const [messages, setMessages] = useState<Message[]>([])
  const [projectId, setProjectId] = useState<string | null>(null)
  const [busy, setBusy] = useState(false)
  const [hasModel, setHasModel] = useState(false)
  const [settingsOpen, setSettingsOpen] = useState(false)

  const abort = useRef<AbortController | null>(null)

  const say = useCallback((message: MessageDraft) => {
    const id = nextId()
    setMessages((prev) => [...prev, { ...message, id } as Message])
    return id
  }, [])

  const amend = useCallback((id: string, patch: MessagePatch) => {
    setMessages((prev) => prev.map((m) => (m.id === id ? ({ ...m, ...patch } as Message) : m)))
  }, [])

  useEffect(() => {
    api
      .connect()
      .then(async (next) => {
        setConnection(next)
        const caps = await api.capabilities().catch(() => null)
        setHasModel(Boolean(caps?.llm.available))
        say({
          role: 'assistant',
          kind: 'text',
          text: caps?.llm.available
            ? GREETING_WITH_MODEL(caps.llm.provider)
            : GREETING_WITHOUT_MODEL,
        })
      })
      .catch((error: Error) => setFailure(error.message))
    return () => abort.current?.abort()
  }, [say])

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
        amend(id, { kind: 'text', text: `没能跟上进度：${(error as Error).message}` })
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

      const [scenes, quality] = await Promise.all([
        api.scenes(final.project_id),
        api.quality(final.project_id).catch(() => null),
      ])
      say({
        role: 'assistant',
        kind: 'video',
        text: '好了。',
        projectId: final.project_id,
        scenes,
        quality,
      })
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
        amend(thinking, { kind: 'text', text: `解析失败：${(error as Error).message}` })
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
        const { job_id } = await api.runAgent(projectId, text)
        await follow(job_id, '好，我来改。')
      } catch (error) {
        say({ role: 'assistant', kind: 'text', text: `没能开始：${(error as Error).message}` })
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
        const { job_id } = await api.submitNarrations(projectId, narrations)
        await follow(job_id, '开始了，渲染要几分钟。')
      } catch (error) {
        amend(id, { locked: false })
        say({ role: 'assistant', kind: 'text', text: `没能开始：${(error as Error).message}` })
      } finally {
        setBusy(false)
      }
    },
    [amend, follow, projectId, say],
  )

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
