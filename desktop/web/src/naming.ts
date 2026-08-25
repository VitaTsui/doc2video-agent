/**
 * What to call a document on screen.
 *
 * A deck's title is its own if it has one and its filename if it does not, and
 * a filename carries whatever the thing that produced it left on the front:
 * 「1786709904848_石化AI商业情报中心-揭榜方案V1.pdf」 is one real example, and in
 * a 240px sidebar the useful half is the half that gets truncated away.
 *
 * Only leading noise is removed — a run of digits with a separator after it,
 * and the extension. A name that is genuinely a number keeps it, because
 * stripping that would leave nothing.
 */
export function readableTitle(name: string): string {
  const withoutExtension = name.replace(/\.(pdf|pptx?|PDF|PPTX?)$/, '')
  const trimmed = withoutExtension.replace(/^\d{6,}[_\-\s]+/, '')
  return trimmed.trim() || withoutExtension.trim() || name
}
