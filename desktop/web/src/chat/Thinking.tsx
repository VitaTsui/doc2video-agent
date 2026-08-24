/**
 * What it is thinking, while it thinks it.
 *
 * Not the account of the run — that is `LedgerCard`, and it lives in the panel
 * where its players, page renders and quality report have room. Putting it in
 * the conversation put a filing cabinet in the middle of a chat: thirty rows
 * with counts, durations and skill names, most of them openable, none of them
 * saying anything in a sentence.
 *
 * This is the other half of the same record, read out loud. One short line per
 * thing it did, newest at the bottom, the way a chain of thought reads: while
 * the run is on it shows the tail and follows itself down, and when the run
 * ends it folds into one line — 「已思考 8 分 12 秒」 — which opens again if
 * you want to know what it was doing all that time.
 */

import { useEffect, useRef, useState } from 'react'

import { ChevronIcon } from '../Icon'
import type { JobState, LedgerEntry } from '../api'

/** One line of the chain. */
type Thought = { key: string; text: string; lead?: boolean }

export function Thinking({ entries, job }: { entries: LedgerEntry[]; job: JobState | null }) {
  const running = !job || job.status === 'queued' || job.status === 'running'
  // `null` means "whatever the run is doing": open while it works, folded once
  // it stops. A click pins it either way — someone reading step twelve should
  // not have the panel shut under them the moment the render finishes.
  const [pinned, setPinned] = useState<boolean | null>(null)
  const open = pinned ?? running
  const lines = thoughts(entries)
  const body = useRef<HTMLDivElement>(null)

  // Follow the newest line, the way a transcript follows the newest message.
  useEffect(() => {
    if (running && body.current) body.current.scrollTop = body.current.scrollHeight
  }, [lines.length, running])

  if (lines.length === 0) return null

  const worked = entries
    .filter((entry) => entry.kind === 'stage')
    .reduce((sum, entry) => sum + entry.duration_s, 0)
  const last = lines[lines.length - 1]

  return (
    <div className="chain">
      <button type="button" className="chain__head" onClick={() => setPinned(!open)}>
        <ChevronIcon open={open} size={14} />
        {running ? (
          // While it works the header is the live line itself, so a folded
          // chain still says what is happening rather than only that something
          // is. 「正在思考」 for four minutes is the thing this replaces.
          <span className="chain__live">{open ? '正在思考…' : last.text}</span>
        ) : (
          <span>
            {'已思考 '}
            <span className="chain__time">{spell(worked)}</span>
          </span>
        )}
      </button>

      {open && (
        <div ref={body} className={running ? 'chain__body chain__body--live' : 'chain__body'}>
          {lines.map((line) => (
            <div key={line.key} className={line.lead ? 'chain__line chain__line--lead' : 'chain__line'}>
              {line.text}
            </div>
          ))}
        </div>
      )}
    </div>
  )
}

/** How much a step got through, counted from the calls it made. */
type Tally = { groups: number; chars: number; subtitles: number }

/**
 * What a step is called once it is over.
 *
 * Said in the tense of something finished, and carrying the counts of
 * everything that went into it — because the lines those counts came from are
 * about to be folded away.
 */
const STAGE_DONE: Record<string, (stage: LedgerEntry, tally: Tally) => string> = {
  解析文档: (s) => `读完了这份文档，${s.artifacts.length} 页`,
  理解结构: (s) => `看清了每页在讲什么，${s.artifacts.length} 页`,
  生成讲稿: (s) => `讲稿写完了，${s.artifacts.length} 页`,
  采用讲稿: (s) => `用了你写的讲稿，${s.artifacts.length} 页`,
  配音: (_s, t) => `念完了，${t.groups} 段，${t.chars} 字`,
  设计镜头: (_s, t) => `${t.groups} 页的框选都想好了`,
  编排时间轴: (_s, t) => `时间轴排好了，${t.subtitles} 条字幕`,
  渲染合成: (_s, t) => `渲染完了，${t.groups} 段`,
  质检: () => '自己把成片看了一遍',
}

/**
 * Turn the record into sentences.
 *
 * Three things happen here.
 *
 * Repeated calls fold: a page is spoken in a dozen units, and a chain that
 * said 「念一段」 five hundred times is a chain nobody reads.
 *
 * Each remaining call is said as what it was, not as `tts:macos_say · 120 字`.
 *
 * And a step that is over takes its own routine work with it — thirty lines
 * of 「想第 N 页该框哪里」 are worth watching while they happen and worth
 * nothing afterwards, so when the step concludes they collapse into its one
 * line and only what actually happened stays: a page re-written, a clip
 * re-recorded, something that failed.
 */
function thoughts(entries: LedgerEntry[]): Thought[] {
  const lines: (Thought & { parent: number; notable: boolean })[] = []
  let held: { call: LedgerEntry; chars: number; parts: number; at: number } | null = null
  // How many times each tool has been reached for. A clip and a rendered
  // segment are named after their scene — `scn_9f3c…`, which says nothing to
  // anyone — while where they fall in the film says everything.
  const nth = new Map<string, number>()
  const tallies = new Map<number, Tally>()

  const tally = (parent: number) => {
    const found = tallies.get(parent) ?? { groups: 0, chars: 0, subtitles: 0 }
    tallies.set(parent, found)
    return found
  }

  const flush = () => {
    if (!held) return
    const text = say(held.call, held.chars, held.parts, held.at)
    const counts = tally(held.call.parent)
    counts.groups += 1
    counts.chars += held.chars
    counts.subtitles += subtitles(held.call.detail)
    if (text)
      lines.push({
        key: `c${held.call.seq}`,
        text,
        parent: held.call.parent,
        notable: notable(held.call),
      })
    held = null
  }

  for (const entry of entries) {
    if (entry.kind === 'call') {
      // Same tool, same page — one thing being done, however many requests it
      // took. `covers` is what a call says it is working on, so this folds the
      // dozen speech units of a page and keeps two pages apart.
      const same =
        held &&
        held.call.name === entry.name &&
        String(held.call.covers) === String(entry.covers) &&
        entry.covers?.length > 0
      if (same && held) {
        held.chars += chars(entry.detail)
        held.parts += 1
        continue
      }
      flush()
      const count = (nth.get(entry.name) ?? 0) + 1
      nth.set(entry.name, count)
      held = { call: entry, chars: chars(entry.detail), parts: 1, at: count }
      continue
    }

    flush()
    if (entry.kind === 'stage') {
      // The step is over: its routine work goes with it, and what was worth
      // remarking on stays.
      for (let index = lines.length - 1; index >= 0; index -= 1) {
        if (lines[index].parent === entry.seq && !lines[index].notable) lines.splice(index, 1)
      }
      const done = STAGE_DONE[entry.name]
      lines.push({
        key: `s${entry.seq}`,
        lead: true,
        parent: -1,
        notable: true,
        text: done ? done(entry, tally(entry.seq)) : entry.name,
      })
      continue
    }
    // A decision, a degradation, a note: already written as a sentence by
    // whatever made it.
    const mark = entry.kind === 'degradation' ? '不得不降级：' : ''
    lines.push({
      key: `n${entry.seq}`,
      parent: -1,
      notable: true,
      text: `${mark}${entry.name}${entry.detail ? `，${entry.detail}` : ''}`,
    })
  }
  flush()
  return lines
}

/**
 * Worth keeping after its step has ended.
 *
 * Everything routine is what the step's own line already says. What is left is
 * what someone would want to know a week later: the page that had to be
 * written twice, the clip that came out wrong, the thing that failed.
 */
function notable(call: LedgerEntry): boolean {
  return (
    call.status === 'failed' ||
    call.name === 'voice:redo' ||
    call.name.startsWith('check:') ||
    /返工|压到|改写/.test(call.detail)
  )
}

/** How many subtitles a timeline call said it laid down. */
function subtitles(detail: string): number {
  const found = detail.match(/(\d+)\s*条字幕/)
  return found ? Number(found[1]) : 0
}

/** One call, said as the thing it was. */
function say(call: LedgerEntry, charCount: number, parts: number, at: number): string {
  const page = pageOf(call)
  const failed = call.status === 'failed' ? '，没成功' : ''

  if (call.name.startsWith('tts:'))
    return `念${page ?? `第 ${at} 段`}${charCount ? `，${charCount} 字` : ''}${
      parts > 1 ? `，${parts} 句` : ''
    }${failed}`
  if (call.name === 'voice:redo') return `${page ?? `第 ${at} 段`}念得不对，重念一次${failed}`
  if (call.name === 'director') return `想${page ?? '这一页'}该框哪里${failed}`
  if (call.name === 'timeline') return `${call.detail.replace(/｜/g, '，')}${failed}`
  if (call.name.startsWith('renderer:')) return `渲染第 ${at} 段${failed}`
  if (call.name.startsWith('check:')) return `查了${call.name.slice(6)}：${call.detail}${failed}`

  // Anything else is the model being asked something, and the call already
  // says what for: 「第 7 页｜返工」, 「第 3 页｜压到 180 字」.
  const what = call.detail.replace(/｜/g, '，')
  return what ? `${what}${failed}` : `想了想${failed}`
}

/** `page:7` → 「第 7 页」, and a range of pages from the call's own words. */
function pageOf(call: LedgerEntry): string | null {
  const spread = call.detail.match(/第\s*\d+(-\d+)?\s*页/)
  if (spread) return spread[0]
  const key = (call.covers ?? []).find((cover) => cover.startsWith('page:'))
  return key ? `第 ${key.slice(5)} 页` : null
}

/** How many characters a call said it handled, if it said. */
function chars(detail: string): number {
  const found = detail.match(/(\d+)\s*字/)
  return found ? Number(found[1]) : 0
}

/** Seconds as something read rather than counted. */
function spell(seconds: number): string {
  if (seconds < 60) return `${Math.round(seconds)} 秒`
  const minutes = Math.floor(seconds / 60)
  const rest = Math.round(seconds - minutes * 60)
  return rest ? `${minutes} 分 ${rest} 秒` : `${minutes} 分`
}
