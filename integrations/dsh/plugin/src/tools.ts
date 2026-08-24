/**
 * The five tools the model gets.
 *
 * They are the whole product surface here, so their descriptions carry the
 * three things a caller cannot infer and gets wrong every time without being
 * told: this service holds no model and the script is the caller's to write;
 * the per-page budget is a cap rather than a suggestion, because audio length
 * cannot be edited after synthesis; and rendering takes minutes, so the tools
 * that start work return a job id instead of waiting.
 *
 * @module dsh-plugin-doc2video/tools
 */

import type { Context } from '@deepseek-ai/cordis'
import type { JsonValue } from '@deepseek-ai/dsh-tools'
import { defineTool } from '@deepseek-ai/dsh-tools'
import { Backend, BackendError } from './backend.ts'
import { follow } from './jobs.ts'

/** One page as the model needs to see it to write for it. */
interface PageView {
  index: number
  title: string
  page_type: string
  summary: string
  narration: string
  elements: { id: string; kind: string; text: string }[]
}

interface PrepareReply {
  project_id: string
  title: string
  topic: string
  intent: { duration?: number }
  duration_stated: boolean
  pages: PageView[]
}

/** One row of the per-page writing budget, as `narration-guide` names them. */
interface GuideRow {
  page: number
  /** Time spoken, which is what the script has to fit. */
  target_seconds: number
  target_chars: number
  /** How long the page is on screen: spoken time plus the silence at each end. */
  page_seconds: number
}

interface JobReply {
  job_id: string
  status: string
}

/** Text, as the model reads a result. */
const asText = (value: unknown) => [{ type: 'text' as const, text: JSON.stringify(value, null, 2) }]

/** Whatever the caller passed as a page/scene map, as strings. */
function asStrings(input: Record<string, unknown>): Record<string, string> {
  return Object.fromEntries(
    Object.entries(input).map(([key, value]) => [key, typeof value === 'string' ? value : String(value)]),
  )
}

/** Say what went wrong in the service's own words. */
function explain(error: unknown): never {
  if (error instanceof BackendError) throw new Error(`doc2video: ${error.message}（${error.code}）`)
  throw error
}

export function registerTools(ctx: Context, backend: Backend): void {
  /**
   * Hand a started render to dsh's background runtime, and say which way this
   * composition ended up working. With `ctx.jobs` the model is notified when
   * the render lands and can stop it with `job_kill`; without it, the only way
   * to know is to ask.
   */
  const handOff = (job: JobReply, label: string, exec: { agent?: unknown }) => {
    const dshJob = follow(ctx, backend, job.job_id, label, exec.agent as never)
    return {
      ...job,
      ...dshJob ? { dsh_job_id: dshJob } : {},
      note: dshJob
        ? '已挂到后台，跑完会通知你，不用反复查；想停用 job_kill。'
        : '隔一会儿用 doc2video_status 查一次进度。',
    }
  }

  ctx.tools.register(defineTool({
    name: 'doc2video_prepare',
    description:
      '解析本机的一份 PDF / PPT / PPTX，返回逐页内容和每页的讲稿预算。'
      + '这个服务不持有模型，讲稿要你自己写——所以这里一次把页面文字、元素和每页的秒数/字数上限全给你，'
      + '你据此逐页写好，再交给 doc2video_render。'
      + '文件按本机路径给，不要 base64。',
    parameters: {
      path: { type: 'string', required: true, description: '本机上的 PDF / PPT / PPTX 绝对路径。' },
      brief: {
        type: 'string',
        description: '想要什么样的视频，例如「面向投资人，八分钟」。说了时长就按这个时长分配预算，没说就按文档内容估。',
      },
    },
    output: { schema: { type: 'json' }, render: (_args, value) => asText(value) },
    async execute(args, exec) {
      try {
        const { upload_id } = await backend.upload(args.path, exec.signal)
        const prepared = await backend.json<PrepareReply>(
          '/agent/prepare',
          { upload_id, brief: args.brief ?? '' },
          exec.signal,
        )
        const guide = await backend.request<{ items: GuideRow[] }>(
          `/projects/${prepared.project_id}/narration-guide`,
          {},
          exec.signal,
        )
        const budget = new Map(guide.items.map(row => [row.page, row]))
        return {
          project_id: prepared.project_id,
          title: prepared.title,
          topic: prepared.topic,
          duration_seconds: prepared.intent.duration ?? 0,
          // Whether that number is a promise or an estimate. A model told
          // 「八分钟」 by a default it invented will compress a script nobody
          // asked it to compress.
          duration_is_yours: prepared.duration_stated,
          pages: prepared.pages.map(page => ({
            index: page.index,
            title: page.title,
            page_type: page.page_type,
            summary: page.summary,
            narration: page.narration,
            elements: page.elements,
            budget_seconds: budget.get(page.index)?.target_seconds ?? 0,
            budget_chars: budget.get(page.index)?.target_chars ?? 0,
          })),
        }
      } catch (error) {
        return explain(error)
      }
    },
  }))

  ctx.tools.register(defineTool({
    name: 'doc2video_render',
    description:
      '把写好的逐页讲稿交回去，开始配音、镜头设计、渲染和质检。'
      + '键是页码（字符串），值是那一页的讲稿；没给的页会用它自己已有的讲稿。'
      + '每页别超过 doc2video_prepare 给的 budget_chars——超了成片就超时长，而音频一旦合成，长度就改不动了。'
      + '渲染要几分钟，所以这里立刻返回 job_id。'
      + '装了 dsh 的后台任务插件时它会挂到后台、跑完通知你——不要反复查；否则再用 doc2video_status 隔一会儿查一次。',
    parameters: {
      project_id: { type: 'string', required: true, description: 'doc2video_prepare 返回的工程 id。' },
      narrations: {
        type: 'object',
        additionalProperties: true,
        required: true,
        description: '页码 -> 该页讲稿，例如 {"1": "……", "2": "……"}。',
      },
    },
    output: { schema: { type: 'json' }, render: (_args, value) => asText(value) },
    async execute(args, exec) {
      try {
        const job = await backend.json<JobReply>(
          `/projects/${args.project_id}/narrations`,
          { narrations: asStrings(args.narrations) },
          exec.signal,
        )
        const pages = Object.keys(args.narrations).length
        return handOff(job, `doc2video 出片：${args.project_id}（${pages} 页）`, exec)
      } catch (error) {
        return explain(error)
      }
    },
  }))

  ctx.tools.register(defineTool({
    name: 'doc2video_status',
    description:
      '查一个 doc2video_render / doc2video_revise 任务到哪一步了。'
      + 'status 是 queued / running / succeeded / failed / cancelled，stage 是当前阶段，done/total 是这一阶段的进度。'
      + '一份三十页的文档整轮下来通常十几到二十几分钟，隔一会儿查一次就好。',
    parameters: {
      job_id: { type: 'string', required: true, description: '起任务时返回的 job_id。' },
    },
    output: { schema: { type: 'json' }, render: (_args, value) => asText(value) },
    async execute(args, exec) {
      try {
        return await backend.request(`/jobs/${args.job_id}`, {}, exec.signal)
      } catch (error) {
        return explain(error)
      }
    },
  }))

  ctx.tools.register(defineTool({
    name: 'doc2video_revise',
    description:
      '只改某几个场景的讲稿，只重做这几个场景——其余场景的配音和画面原样保留。'
      + '键是场景 id（doc2video_result 里有），值是新讲稿。一次调用是一个任务，别一个场景调一次。',
    parameters: {
      project_id: { type: 'string', required: true, description: '工程 id。' },
      scenes: {
        type: 'object',
        additionalProperties: true,
        required: true,
        description: '场景 id -> 新讲稿。',
      },
    },
    output: { schema: { type: 'json' }, render: (_args, value) => asText(value) },
    async execute(args, exec) {
      try {
        const job = await backend.json<JobReply>(
          `/projects/${args.project_id}/scenes/narrations`,
          { scenes: asStrings(args.scenes) },
          exec.signal,
        )
        const count = Object.keys(args.scenes).length
        return handOff(job, `doc2video 重做 ${count} 个场景：${args.project_id}`, exec)
      } catch (error) {
        return explain(error)
      }
    },
  }))

  ctx.tools.register(defineTool({
    name: 'doc2video_result',
    description:
      '一次生成之后值得知道的一切：成片在哪、多长、每个场景的 id 和讲稿、质量分和质检结论。'
      + '要改哪一页，从这里拿场景 id 交给 doc2video_revise。',
    parameters: {
      project_id: { type: 'string', required: true, description: '工程 id。' },
    },
    output: { schema: { type: 'json' }, render: (_args, value) => asText(value) },
    async execute(args, exec) {
      try {
        const [project, scenes, quality] = await Promise.all([
          backend.request<{ output_path?: string; document?: { title?: string } }>(
            `/projects/${args.project_id}`, {}, exec.signal,
          ),
          backend.request<{ items: { scene_id: string; source_page: number; narration: string; duration: number }[] }>(
            `/projects/${args.project_id}/scenes`, {}, exec.signal,
          ),
          backend.request<JsonValue>(`/projects/${args.project_id}/quality`, {}, exec.signal)
            .catch((): JsonValue => null),
        ])
        const seconds = scenes.items.reduce((sum, scene) => sum + scene.duration, 0)
        return {
          project_id: args.project_id,
          title: project.document?.title ?? '',
          // A path only when this process owns the server and therefore knows
          // where it keeps things; otherwise the file is on another machine
          // and only the URL is true.
          output_path: backend.storageDir && project.output_path
            ? `${backend.storageDir}/${project.output_path}`
            : '',
          output_url: project.output_path ? `/projects/${args.project_id}/video` : '',
          duration_seconds: Math.round(seconds),
          scenes: scenes.items.map(scene => ({
            scene_id: scene.scene_id,
            page: scene.source_page,
            seconds: Math.round(scene.duration * 10) / 10,
            narration: scene.narration,
          })),
          quality,
        }
      } catch (error) {
        return explain(error)
      }
    },
  }))
}
