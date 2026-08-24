/**
 * A render, as a background job dsh already knows how to manage.
 *
 * Rendering a thirty-page deck takes fifteen to twenty-five minutes. Left to
 * itself the model spends that time calling `doc2video_status` in a loop —
 * every call a turn, every turn tokens, and the interval is guesswork. dsh has
 * a runtime for exactly this: register the work with `ctx.jobs` and the model
 * is *told* when it finishes, lists it with `job_list`, and stops it with
 * `job_kill`.
 *
 * The producer here owns no process. The work lives in the backend and is
 * already identified by its own job id; this polls it, translates its ending
 * into an outcome, and forwards a kill.
 *
 * All of it is optional. A composition without `ctx.jobs` (or without a
 * controller serving this agent) still gets a job id back and can poll —
 * degraded, not broken.
 *
 * @module dsh-plugin-doc2video/jobs
 */

import type { Context } from '@deepseek-ai/cordis'
import type { ToolRunContext } from '@deepseek-ai/dsh-tools'
import type { JobOutcome } from '@deepseek-ai/dsh-jobs'
import { setTimeout as delay } from 'node:timers/promises'
import type { Backend } from './backend.ts'

declare module '@deepseek-ai/dsh-jobs' {
  interface JobKindMap {
    doc2video: 'doc2video'
  }
}

/**
 * Who owns a job. Taken from the tool's own execution context rather than from
 * `@deepseek-ai/dsh-agent`: this is a type, and depending on that package for
 * it drags in an rc version line that disagrees with the one `dsh-tools`
 * resolves.
 */
type Owner = NonNullable<ToolRunContext['agent']>

/** How often to ask the backend where it has got to. */
const POLL_MS = 3000

/** What `GET /jobs/{id}` answers. */
interface JobView {
  job_id: string
  status: 'queued' | 'running' | 'succeeded' | 'failed' | 'cancelled'
  stage: string
  detail: string
  done: number
  total: number
  error?: { message?: string }
}

const STAGE_LABEL: Record<string, string> = {
  parse: '解析文档',
  understand: '理解结构',
  narrate: '生成讲稿',
  voice: '配音',
  direct: '设计镜头',
  motion: '编排时间轴',
  render: '渲染合成',
  review: '质检',
}

/** Where it is now, in one line. */
function where(view: JobView): string {
  const stage = STAGE_LABEL[view.stage] ?? view.stage
  const progress = view.total > 0 ? `${view.done}/${view.total}` : view.detail
  return progress ? `${stage} ${progress}` : stage
}

/**
 * Register a running backend job with dsh, if this composition has anywhere to
 * register it.
 * @returns the dsh job id, or an empty string when nothing took it.
 */
export function follow(
  ctx: Context,
  backend: Backend,
  jobId: string,
  label: string,
  owner: Owner | undefined,
): string {
  const jobs = ctx.get('jobs')
  if (jobs === undefined) return ''

  try {
    return jobs.start({
      kind: 'doc2video',
      label,
      ...owner ? { owner } : {},
      run: () => {
        // Stopping is a request, not a kill: the scene being rendered right
        // now finishes, because a half-written clip is one the incremental
        // render would later mistake for a good one. So `cancel` asks, and
        // `done` settles when the poll sees it stop.
        let stopping = false
        const done = (async (): Promise<JobOutcome> => {
          for (;;) {
            await delay(POLL_MS)
            let view: JobView
            try {
              view = await backend.request<JobView>(`/jobs/${jobId}`)
            } catch (error) {
              // A backend that went away mid-render is a failed job, not a
              // job that hangs forever.
              return { status: 'failed', detail: String(error) }
            }
            if (view.status === 'succeeded') {
              return { status: 'completed', output: `成片好了（${jobId}），用 doc2video_result 取结果` }
            }
            if (view.status === 'cancelled') {
              return { status: 'killed', detail: '已中止，做好的部分保留' }
            }
            if (view.status === 'failed') {
              return { status: 'failed', detail: view.error?.message ?? view.detail }
            }
            if (stopping) continue
          }
        })()
        return {
          cancel: (): void => {
            stopping = true
            void backend.request(`/jobs/${jobId}/cancel`, { method: 'POST' }).catch(() => undefined)
          },
          done,
        }
      },
    })
  } catch {
    // No controller serves this agent — the composition simply does not do
    // background jobs. Polling still works.
    return ''
  }
}

export { where }
