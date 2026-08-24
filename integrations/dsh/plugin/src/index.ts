/**
 * doc2video as a dsh plugin: five tools, and a backend the harness owns.
 *
 * The MCP bridge in the directory above already makes this service usable from
 * dsh without any code. This exists for the two things that bridge cannot do.
 *
 * **The file stays a file.** Over MCP a deck arrives base64-encoded inside a
 * tool call, because an MCP caller may be on another machine. dsh is on this
 * one, so `doc2video_prepare` takes a path and the bytes never enter the
 * conversation — which is the difference between working and not working for
 * any deck worth making a video of.
 *
 * **Nobody starts a server.** Configure nothing and the plugin starts the
 * backend itself on a port it picked, with a token it minted, and kills it on
 * disposal. The user runs `dsh`.
 *
 * @module dsh-plugin-doc2video
 */

import type { Context } from '@deepseek-ai/cordis'
import z from '@deepseek-ai/schemastery'
// Side-effect type import: declaration-merges `ctx.tools` onto Context.
import type {} from '@deepseek-ai/dsh-tools'
import { Backend } from './backend.ts'
import { registerTools } from './tools.ts'

/** Cordis plugin name used by loader diagnostics. */
export const name = 'doc2video'

/** Services required by this plugin. */
export const inject = ['tools']

export interface Config {
  /** An already-running backend. Empty starts one. */
  baseUrl: string
  /** Bearer token for an already-running backend. */
  token: string
  /** How to start one. `doc2video` if it is on PATH; otherwise an absolute path. */
  command: string
  /** Arguments before `serve`, e.g. `['run', 'doc2video']` for a uv checkout. */
  args: string[]
  /** Working directory for the spawned backend. */
  cwd: string
  /** Where it keeps projects. Empty leaves the backend's own default. */
  storageDir: string
  /** Extra environment for the spawned backend: voice, speech rate, keys. */
  env: Record<string, string>
  /** How long to wait for a spawned backend to answer `/health`. */
  startupTimeoutMs: number
  /** Per-request timeout. Parsing a thirty-page PDF is the slow one. */
  requestTimeoutMs: number
}

export const Config: z<Config> = z.object({
  baseUrl: z.string().default(''),
  token: z.string().default(''),
  command: z.string().default('doc2video'),
  args: z.array(String).default([]),
  cwd: z.string().default(''),
  storageDir: z.string().default(''),
  env: z.dict(String).default({}),
  startupTimeoutMs: z.number().min(1000).default(60_000),
  requestTimeoutMs: z.number().min(1000).default(180_000),
}) as unknown as z<Config>

/**
 * Reach a backend, then publish the tools.
 *
 * Explicitly `async`: Cordis treats a prototype-bearing ordinary function as a
 * constructor, and a returned Promise would not be awaited as startup work.
 * Activation waits for `/health`, so a composition that activates has a
 * backend — the model never gets a tool that cannot work yet.
 */
export async function apply(ctx: Context, config: Config): Promise<void> {
  const backend = new Backend({
    baseUrl: config.baseUrl,
    token: config.token,
    command: config.command,
    args: config.args,
    cwd: config.cwd,
    storageDir: config.storageDir,
    env: config.env,
    startupTimeoutMs: config.startupTimeoutMs,
    requestTimeoutMs: config.requestTimeoutMs,
  })

  ctx.effect(() => {
    return () => backend.close()
  }, 'doc2video.backend')

  await backend.open()
  registerTools(ctx, backend)
}
