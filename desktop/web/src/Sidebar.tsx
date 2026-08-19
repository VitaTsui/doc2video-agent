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

import type { ProjectSummary } from './api'
import { GearIcon, PanelIcon, PlusIcon } from './Icon'

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

export function Sidebar({
  projects,
  current,
  collapsed,
  onOpen,
  onNew,
  onSettings,
  onToggle,
}: {
  projects: ProjectSummary[]
  current: string | null
  collapsed: boolean
  onOpen: (projectId: string) => void
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
          projects.map((project) => (
            <button
              key={project.project_id}
              type="button"
              className={
                project.project_id === current ? 'sidebar__item sidebar__item--on' : 'sidebar__item'
              }
              onClick={() => onOpen(project.project_id)}
              title={project.title || project.source}
            >
              <span className="sidebar__title">{project.title || project.source || '未命名'}</span>
              <span className="sidebar__meta">
                {when(project.updated_at)}
                {project.duration > 0 && ` · ${Math.round(project.duration)}s`}
                {/* Whether there is something to watch is the one fact worth
                    carrying in a list this narrow. */}
                {!project.output && ' · 未出片'}
              </span>
            </button>
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
