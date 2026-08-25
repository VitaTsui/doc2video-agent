/**
 * The record, cut back into the cards it was watched in.
 *
 * A finished project keeps almost nothing in its conversation — one line, the
 * sentence someone typed. Everything that happened afterwards was said by the
 * ledger: three hundred entries across four runs, every stage with its own
 * duration. Reopening the project replayed only the conversation, so a film
 * that took forty minutes to make came back as a document card and nothing
 * else, and the question 「它那四十分钟在干什么」 had no answer on screen.
 *
 * So the record is read back out. The cuts are the ones the live view makes:
 * a new card at every run, because a run is a thing someone asked for, and a
 * new card at 设计镜头 / 编排时间轴 / 渲染合成, because each is its own wait and
 * one card counting 0→30 four times reads as one thing starting over.
 *
 * The cut has to be decided late. A stage is written down when it *ends* — that
 * is where its duration comes from — so the calls belonging to 设计镜头 arrive
 * before the line that names it. Entries are held until a stage line says what
 * they were, and only then does the group know which card it opens.
 */

import type { LedgerEntry } from '../api'

/** Stages that begin a card of their own, as they do while it runs. */
const BEATS = new Set(['设计镜头', '编排时间轴', '渲染合成'])

export function replay(entries: LedgerEntry[]): LedgerEntry[][] {
  const cards: LedgerEntry[][] = []
  let card: LedgerEntry[] = []
  let held: LedgerEntry[] = []

  const close = (stage: LedgerEntry | null) => {
    if (held.length === 0) return
    const opens =
      card.length > 0 &&
      (card[0].run_id !== held[0].run_id || (stage !== null && BEATS.has(stage.name)))
    if (opens) {
      cards.push(card)
      card = []
    }
    card = card.concat(held)
    held = []
  }

  for (const entry of entries) {
    held.push(entry)
    if (entry.kind === 'stage') close(entry)
  }
  // Whatever a run was in the middle of when it stopped. A run that failed
  // halfway has no closing stage line, and its work is the most worth seeing.
  close(null)
  if (card.length > 0) cards.push(card)
  return fold(cards)
}

/** Under this many seconds, a step was not a wait. */
const BRIEF = 5

/**
 * A beat that turned out to be no wait at all is not a beat.
 *
 * 设计镜头 and 编排时间轴 are decided rather than generated — four hundredths of
 * a second, and one second. While the run is on they still earn a card,
 * because you are watching and they are what is happening. Read back
 * afterwards they were two cards reading 「已思考 0 秒」 and 「已思考 1 秒」,
 * between two that took ten minutes each. They fold into the card before them,
 * where their own line — 「30 页的框选都想好了」 — still says what they did.
 */
function fold(cards: LedgerEntry[][]): LedgerEntry[][] {
  const kept: LedgerEntry[][] = []
  for (const card of cards) {
    const worked = card
      .filter((entry) => entry.kind === 'stage')
      .reduce((sum, entry) => sum + entry.duration_s, 0)
    const before = kept[kept.length - 1]
    // Never across runs: two runs are two things someone asked for, however
    // fast either of them was.
    if (before && worked < BRIEF && before[0].run_id === card[0].run_id) {
      kept[kept.length - 1] = before.concat(card)
      continue
    }
    kept.push(card)
  }
  return kept
}

/** Which card the document came out of, so the deck card can follow it. */
export function parsedIn(cards: LedgerEntry[][]): number {
  return cards.findIndex((card) =>
    card.some((entry) => entry.kind === 'stage' && entry.name === '解析文档'),
  )
}
