/**
 * Every video this machine has made, and the way back into any of them.
 *
 * Until now the window held exactly one conversation — the last one — and the
 * only way to reach an earlier deck was to remember nothing about it. The
 * projects were all on disk the whole time; nothing showed them.
 *
 * Deliberately not a file tree. A project is one deck, one script, one video:
 * there is no hierarchy to model, so the list is a list, ordered by when it
 * was last touched, which is the order someone looks for things in.
 */

// Deep import, as elsewhere: the barrel pulls half the library in with it.
import SecondConf from '@hsu-react/ui/es/components/SecondConf'
import { readableTitle } from './naming'

import type { ProjectSummary } from './api'
import { GearIcon, PanelIcon, PlusIcon, TrashIcon } from './Icon'

function when(iso: string | null): string {
  if (!iso) return ''
  const then = new Date(iso)
  const minutes = Math.round((Date.now() - then.getTime()) / 60000)
  if (minutes < 1) return '刚刚'
  if (minutes < 60) return `${minutes} 分钟前`
  if (minutes < 60 * 24) return `${Math.round(minutes / 60)} 小时前`
  if (minutes < 60 * 24 * 30) return `${Math.round(minutes / 1440)} 天前`
  return `${then.getFullYear()}/${then.getMonth() + 1}/${then.getDate()}`
}

/**
 * Which run of days a project belongs to.
 *
 * Thirteen rows of the same deck is what iterating on one document looks like,
 * and no amount of naming makes them tell each other apart. What does is when
 * they happened — the same thing every chat does with its history, and the
 * reason its list is readable at thirty entries.
 */
function period(iso: string | null): string {
  if (!iso) return '更早'
  const then = new Date(iso)
  const today = new Date()
  const midnight = new Date(today.getFullYear(), today.getMonth(), today.getDate())
  const days = Math.floor((midnight.getTime() - then.getTime()) / 86_400_000)
  if (days < 0) return '今天'
  if (days < 1) return '昨天'
  if (days < 7) return '最近七天'
  if (days < 30) return '最近一个月'
  return '更早'
}

export function Sidebar({
  projects,
  current,
  collapsed,
  onOpen,
  onDelete,
  onNew,
  onSettings,
  onToggle,
}: {
  projects: ProjectSummary[]
  current: string | null
  collapsed: boolean
  onOpen: (projectId: string) => void
  onDelete: (project: ProjectSummary) => void
  onNew: () => void
  onSettings: () => void
  onToggle: () => void
}) {
  if (collapsed) {
    return (
      <aside className="sidebar sidebar--collapsed">
        <button type="button" className="sidebar__icon" title="展开侧边栏" onClick={onToggle}>
          <PanelIcon open={false} size={20} />
        </button>
        <button type="button" className="sidebar__icon" title="新会话" onClick={onNew}>
          <PlusIcon size={20} />
        </button>
        <div style={{ marginTop: 'auto' }}>
          <button type="button" className="sidebar__icon" title="设置" onClick={onSettings}>
            <GearIcon size={19} />
          </button>
        </div>
      </aside>
    )
  }

  return (
    <aside className="sidebar">
      <div className="sidebar__head">
        <span className="sidebar__brand">Doc2Video</span>
        <button type="button" className="sidebar__icon" title="收起侧边栏" onClick={onToggle}>
          <PanelIcon open size={20} />
        </button>
      </div>

      <button type="button" className="sidebar__new" onClick={onNew}>
        <PlusIcon size={17} />
        新会话
      </button>

      <div className="sidebar__section">工程</div>

      <div className="sidebar__list">
        {projects.length === 0 ? (
          // Not an error state, and not styled like one: before the first deck
          // there is genuinely nothing here.
          <p className="muted" style={{ padding: '0 12px' }}>
            还没有工程。拖一份 PPT 或 PDF 进来就开始了。
          </p>
        ) : (
          projects.map((project, index) => (
            <div key={project.project_id}>
            {period(project.updated_at) !== period(projects[index - 1]?.updated_at ?? null)
              || index === 0 ? (
              <div className="sidebar__period">{period(project.updated_at)}</div>
            ) : null}
            <div
              className={
                project.project_id === current ? 'sidebar__row sidebar__row--on' : 'sidebar__row'
              }
            >
              <button
                type="button"
                className="sidebar__item"
                onClick={() => onOpen(project.project_id)}
                title={readableTitle(project.title || project.source)}
              >
                <span className="sidebar__title">
                  {readableTitle(project.title || project.source) || '未命名'}
                </span>
                <span className="sidebar__meta">
                  {when(project.updated_at)}
                  {project.duration > 0 && ` · ${Math.round(project.duration)}s`}
                  {/* Whether there is something to watch is the one fact worth
                      carrying in a list this narrow. */}
                  {!project.output && ' · 未出片'}
                </span>
              </button>
              {/* Appears on hover: a delete that is always visible in a list
                  is a delete that eventually gets hit by accident. And it asks
                  first — in the window rather than through `window.confirm`,
                  which is the webview's dialog and not the app's.

                  The trigger form of SecondConf keeps its own open state, so a
                  list of any length needs no flag per row, and it stops the
                  click from reaching the row underneath — which would
                  otherwise open the very project being deleted. */}
              <SecondConf
                contentTitle={`删除《${readableTitle(project.title || project.source) || '这个工程'}》`}
                contentText="生成出来的视频、讲稿和音频都会一起清掉，上传的原文件不动。"
                okText="删除"
                cancelText="不删了"
                onOk={() => onDelete(project)}
              >
                <button type="button" className="sidebar__delete" title="删除这个工程">
                  <TrashIcon size={15} />
                </button>
              </SecondConf>
            </div>
            </div>
          ))
        )}
      </div>

      <button type="button" className="sidebar__foot" onClick={onSettings}>
        <GearIcon size={17} />
        设置
      </button>
    </aside>
  )
}
