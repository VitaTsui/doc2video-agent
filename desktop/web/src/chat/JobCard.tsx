/** What the pipeline is doing right now. */

import type { JobState } from '../api'

const STAGE_LABEL: Record<string, string> = {
  parse: '解析文档',
  understand: '理解结构',
  narrate: '生成讲稿',
  voice: '配音',
  direct: '镜头设计',
  motion: '时间轴',
  render: '渲染',
  review: '质检',
  done: '完成',
}

export function JobCard({ job }: { job: JobState | null }) {
  if (!job) return null
  const finished = job.status === 'succeeded' || job.status === 'failed'
  const label = STAGE_LABEL[job.stage] ?? job.stage ?? '排队中'

  return (
    <div className="card">
      <div style={{ display: 'flex', justifyContent: 'space-between', marginBottom: 8 }}>
        <span style={{ display: 'inline-flex', alignItems: 'center', gap: 8 }}>
          {/* Something turning while it works. A bar that fills in bursts,
              minutes apart, looks the same as one that has stopped. */}
          {!finished && <span className="spinner" />}
          {label}
        </span>
        <span className="muted">
          {/* Nothing on the right once it is done. `detail` on a finished job
              is the agent's reply, and that arrives as its own message a line
              below — printing it here too showed the same three paragraphs
              twice, once squeezed into a progress card. */}
          {job.total > 0 ? `${job.done}/${job.total}` : finished ? '' : job.detail}
        </span>
      </div>
      <div className="bar">
        {/* A denominator exists only for the stages that loop over scenes;
            anywhere else a percentage would be invented, so it sweeps. */}
        {job.total > 0 ? (
          <div className="bar__fill" style={{ width: `${(job.done / job.total) * 100}%` }} />
        ) : (
          <div className={finished ? 'bar__fill' : 'bar__fill bar__fill--pulse'} />
        )}
      </div>
    </div>
  )
}
