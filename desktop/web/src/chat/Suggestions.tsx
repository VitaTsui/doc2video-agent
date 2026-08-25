/**
 * What you can say next, as something to press.
 *
 * This product's follow-ups are not guessable. 「第 3 页太长了，压到 20 秒」 does
 * exactly what it says, and nothing on screen suggests a sentence like that is
 * understood — so the greeting explained it in prose, at the one moment nobody
 * reads prose, and never again afterwards.
 *
 * Said as chips instead, beside the buttons, and only while there is nothing
 * running: they teach the interaction and take it at the same time. They also
 * put something in the space a short conversation leaves between the last turn
 * and the composer, which is the other half of why this exists.
 */

export function Suggestions({
  rendered,
  onSay,
}: {
  /** Whether a film exists yet. Before one, the useful sentences are about
   *  the whole thing; after, they are about a page of it. */
  rendered: boolean
  onSay: (text: string) => void
}) {
  const said = rendered
    ? ['第 3 页太长了，压到 20 秒', '语速慢一点', '整体压到八分钟']
    : ['压到八分钟', '面向投资人，重点讲商业价值', '语速快一点']

  return (
    <div className="suggests">
      {said.map((text) => (
        <button key={text} type="button" className="suggests__chip" onClick={() => onSay(text)}>
          {text}
        </button>
      ))}
    </div>
  )
}
