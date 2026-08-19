/**
 * The handful of icons this window draws itself.
 *
 * Written out rather than fetched: Iconify resolves its data over the network,
 * and the desktop CSP allows only the local backend — a blocked request there
 * shows nothing at all, which is how the toolbar once ended up empty. The four
 * the component library needs are baked by `scripts/gen-icons.mjs`; these are
 * ours, and small enough to simply be here.
 *
 * They replace text glyphs (☰ ＋ ⚙ ▸). A glyph is drawn by whatever font the
 * system picked, at whatever weight it has, and next to real controls it reads
 * as punctuation — the size and stroke are not ours to set.
 */

type Props = {
  size?: number
  className?: string
}

function svg(path: React.ReactNode, { size = 18, className }: Props) {
  return (
    <svg
      className={className}
      width={size}
      height={size}
      viewBox="0 0 24 24"
      fill="none"
      stroke="currentColor"
      strokeWidth={1.8}
      strokeLinecap="round"
      strokeLinejoin="round"
      aria-hidden="true"
    >
      {path}
    </svg>
  )
}

/**
 * Collapse and expand, as the panel itself rather than as a hamburger.
 *
 * `☰` says "a menu is behind this", which is not what the control does — it
 * folds the sidebar away. The rectangle with a rail down one side shows both
 * states directly, and the rail changes side so the icon says which way it is
 * about to go.
 */
export const PanelIcon = ({ open, ...props }: Props & { open: boolean }) =>
  svg(
    <>
      <rect x="3" y="4" width="18" height="16" rx="2" />
      {open ? <path d="M9 4v16" /> : <path d="M15 4v16" />}
    </>,
    props,
  )

export const PlusIcon = (props: Props) =>
  svg(
    <>
      <path d="M12 5v14" />
      <path d="M5 12h14" />
    </>,
    props,
  )

export const GearIcon = (props: Props) =>
  svg(
    <>
      <circle cx="12" cy="12" r="3" />
      <path d="M19.4 15a1.65 1.65 0 0 0 .33 1.82l.06.06a2 2 0 1 1-2.83 2.83l-.06-.06a1.65 1.65 0 0 0-1.82-.33 1.65 1.65 0 0 0-1 1.51V21a2 2 0 1 1-4 0v-.09A1.65 1.65 0 0 0 9 19.4a1.65 1.65 0 0 0-1.82.33l-.06.06a2 2 0 1 1-2.83-2.83l.06-.06a1.65 1.65 0 0 0 .33-1.82 1.65 1.65 0 0 0-1.51-1H3a2 2 0 1 1 0-4h.09A1.65 1.65 0 0 0 4.6 9a1.65 1.65 0 0 0-.33-1.82l-.06-.06a2 2 0 1 1 2.83-2.83l.06.06A1.65 1.65 0 0 0 9 4.6 1.65 1.65 0 0 0 10 3.09V3a2 2 0 1 1 4 0v.09A1.65 1.65 0 0 0 15 4.6a1.65 1.65 0 0 0 1.82-.33l.06-.06a2 2 0 1 1 2.83 2.83l-.06.06A1.65 1.65 0 0 0 19.4 9v.09a1.65 1.65 0 0 0 1.51 1H21a2 2 0 1 1 0 4h-.09a1.65 1.65 0 0 0-1.51 1z" />
    </>,
    props,
  )

/**
 * A page with a folded corner — what the panel behind this button holds.
 *
 * The panel's own toggle is the panel shape, because that control folds the
 * panel away. This one opens onto the deck and the video, so it says so.
 */
export const FileIcon = (props: Props) =>
  svg(
    <>
      <path d="M14 3H7a2 2 0 0 0-2 2v14a2 2 0 0 0 2 2h10a2 2 0 0 0 2-2V8z" />
      <path d="M14 3v5h5" />
    </>,
    props,
  )

export const CloseIcon = (props: Props) =>
  svg(
    <>
      <path d="M6 6l12 12" />
      <path d="M18 6L6 18" />
    </>,
    props,
  )

/**
 * A dropdown's arrow: down when shut, up when open.
 *
 * Distinct from `ChevronIcon`, which points right when shut — that is the
 * disclosure sense ("there is more inside this row"), and on a menu it reads
 * as "goes to a submenu" rather than "opens downward".
 */
export const CaretIcon = ({ open, ...props }: Props & { open: boolean }) =>
  svg(<path d={open ? 'M6 15l6-6 6 6' : 'M6 9l6 6 6-6'} />, props)

/** Points right when closed, down when open — the usual disclosure. */
export const ChevronIcon = ({ open, ...props }: Props & { open: boolean }) =>
  svg(<path d={open ? 'M6 9l6 6 6-6' : 'M9 6l6 6-6 6'} />, props)

/**
 * Delete, as a bin rather than an ✕.
 *
 * The ✕ this replaces is the same glyph the panel uses to close itself, and a
 * row that offers "close" where it means "delete for good" is a row that gets
 * clicked by someone expecting the first one.
 */
export const TrashIcon = (props: Props) =>
  svg(
    <>
      <path d="M4 7h16" />
      <path d="M10 4h4" />
      <path d="M6 7l1 12a1 1 0 001 1h8a1 1 0 001-1l1-12" />
      <path d="M10 11v6" />
      <path d="M14 11v6" />
    </>,
    props,
  )
