/**
 * The assistant's words, with the little structure they carry.
 *
 * Not a markdown library. The component library ships one and this repository
 * already refused it: its renderer drags in mermaid, cytoscape and pdf.js —
 * six megabytes of diagram engines to set some text in bold. What replies here
 * actually use is four things: a bold run, a piece of code or a filename, a
 * bulleted list, and a numbered one. Those are cheap.
 *
 * Everything it does not recognise stays exactly as typed, newlines included,
 * because most replies are plain sentences and a renderer that reflowed them
 * would be a downgrade.
 */

import type { ReactNode } from 'react'

/** `**bold**` and `` `code` `` inside one line. */
function inline(text: string, key: string): ReactNode[] {
  const out: ReactNode[] = []
  const pattern = /\*\*(.+?)\*\*|`([^`]+?)`/g
  let at = 0
  let found: RegExpExecArray | null
  let n = 0
  while ((found = pattern.exec(text)) !== null) {
    if (found.index > at) out.push(text.slice(at, found.index))
    n += 1
    if (found[1] !== undefined) out.push(<strong key={`${key}b${n}`}>{found[1]}</strong>)
    else out.push(<code key={`${key}c${n}`}>{found[2]}</code>)
    at = found.index + found[0].length
  }
  if (at < text.length) out.push(text.slice(at))
  return out
}

const BULLET = /^\s*[-*]\s+(.*)$/
const NUMBER = /^\s*(\d+)[.、)]\s+(.*)$/

export function Prose({ text }: { text: string }) {
  const blocks: ReactNode[] = []
  let items: string[] | null = null
  let ordered = false
  let plain: string[] = []

  const flushPlain = () => {
    // A blank line is what separated the paragraph from the list above it;
    // rendered as its own paragraph it becomes an empty one, and the gap
    // doubles.
    while (plain.length && !plain[0].trim()) plain.shift()
    while (plain.length && !plain[plain.length - 1].trim()) plain.pop()
    if (!plain.length) return
    const at = blocks.length
    blocks.push(
      <p key={`p${at}`} className="prose__p">
        {inline(plain.join('\n'), `p${at}`)}
      </p>,
    )
    plain = []
  }

  const flushList = () => {
    if (!items) return
    const at = blocks.length
    const rendered = items.map((item, index) => (
      <li key={index}>{inline(item, `l${at}i${index}`)}</li>
    ))
    blocks.push(
      ordered ? (
        <ol key={`l${at}`} className="prose__list">
          {rendered}
        </ol>
      ) : (
        <ul key={`l${at}`} className="prose__list">
          {rendered}
        </ul>
      ),
    )
    items = null
  }

  for (const line of text.split('\n')) {
    const bullet = BULLET.exec(line)
    const numbered = NUMBER.exec(line)
    if (bullet || numbered) {
      flushPlain()
      const wantsOrdered = Boolean(numbered)
      if (!items || ordered !== wantsOrdered) {
        flushList()
        ordered = wantsOrdered
        items = []
      }
      items.push((bullet ? bullet[1] : (numbered as RegExpExecArray)[2]).trim())
      continue
    }
    flushList()
    plain.push(line)
  }
  flushList()
  flushPlain()

  return <>{blocks}</>
}
