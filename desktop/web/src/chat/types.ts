import type { GuideRow, JobState, LedgerEntry, PageView, Quality, Scene } from '../api'

/**
 * One turn in the conversation.
 *
 * A message's `kind` is what it renders as, and it can change: the same message
 * that says "正在解析文档…" becomes the deck summary when parsing finishes, and
 * the one showing live progress becomes nothing more than a finished bar. That
 * is deliberate — a chat that appends a new bubble for every state change reads
 * as noise, and the user only ever cares about the latest one.
 */
export type Message = {
  id: string
  role: 'user' | 'assistant'
  text: string
} & (
  | { kind: 'text'; file?: string }
  | {
      kind: 'deck'
      projectId: string
      pages: PageView[]
      guide: GuideRow[]
      hasModel: boolean
      /** Set once generation starts, so the card stops accepting edits. */
      locked?: boolean
    }
  | { kind: 'job'; job: JobState | null }
  | {
      kind: 'video'
      projectId: string
      scenes: Scene[]
      quality: Quality | null
      ledger: LedgerEntry[]
    }
)

/**
 * `Omit` collapses a union into its common keys; this maps over the members
 * instead, so a draft message keeps the fields of whichever kind it is.
 */
type Distribute<T, K extends keyof T> = T extends unknown ? Omit<T, K> : never

/** A message before it has an id. */
export type MessageDraft = Distribute<Message, 'id'>

/** A change to one message — any subset of the fields of any one kind. */
export type MessagePatch = Partial<MessageDraft>
