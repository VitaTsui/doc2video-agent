/**
 * The backend, as the window sees it.
 *
 * Everything goes over the same HTTP API an MCP client or a curl script would
 * use — there is no private desktop channel — so the only things this module
 * adds are the per-launch bearer token, one place that knows the backend moved,
 * and two rough edges smoothed over:
 *
 * - **Two error shapes.** FastAPI's `HTTPException` answers with
 *   `{detail: {code, message}}` while the backend's own `Doc2VideoError`
 *   answers with `{code, message, detail}` at the top level. Both come from the
 *   same endpoints. Normalising once here keeps every caller from guessing.
 * - **Progress arrives as SSE.** `EventSource` cannot send an Authorization
 *   header, so the stream is read from `fetch` instead and parsed by hand. It
 *   is a small parser because the server only ever emits `data:` lines and one
 *   `event: done`.
 */

import { invoke } from '@tauri-apps/api/core'

export interface Connection {
  base_url: string
  token: string
}

export interface JobState {
  job_id: string
  status: 'queued' | 'running' | 'succeeded' | 'failed'
  stage: string
  detail: string
  done: number
  total: number
  project_id: string | null
  error: { code: string; message: string } | null
}

export interface PageView {
  index: number
  title: string
  page_type: string
  summary: string
  /** Project-relative path to this page's render, e.g. "assets/page_001.png". */
  image: string | null
  elements: { id: string; kind: string; text: string }[]
}

export interface GuideRow {
  page: number
  title: string
  page_type: string
  target_seconds: number
  target_chars: number
  page_seconds: number
}

export interface Scene {
  scene_id: string
  source_page: number
  title: string
  duration: number
  narration: string
}

export interface LedgerArtifact {
  label: string
  kind: 'image' | 'audio' | 'video' | 'text' | 'json'
  path: string
  text: string
  scene_id: string
}

export interface LedgerEntry {
  seq: number
  kind: 'stage' | 'decision' | 'degradation' | 'note'
  name: string
  detail: string
  status: string
  duration_s: number
  artifacts: LedgerArtifact[]
}

/** How this project got made, step by step, with what each step produced. */
export async function ledger(projectId: string) {
  const body = await request<{ items: LedgerEntry[] }>(`/projects/${projectId}/ledger`)
  return body.items
}

export interface Quality {
  score: number
  errors: number
  warnings: number
  dimensions: { name: string; score: number; weight: number; detail: string }[]
}

/**
 * Whatever was thrown, as something worth showing someone.
 *
 * Tauri's `invoke` rejects with the raw value its command returned — a plain
 * string, not an Error — so reading `.message` off it yields `undefined`. A UI
 * that then renders `error && <card>` shows nothing at all: the install
 * appeared to "just go back to the button" with no explanation, which is the
 * worst way for a 400MB download to fail.
 */
export function describeError(thrown: unknown): string {
  if (typeof thrown === 'string') return thrown
  if (thrown instanceof Error) return thrown.message
  if (thrown && typeof thrown === 'object' && 'message' in thrown) {
    return String((thrown as { message: unknown }).message)
  }
  return String(thrown)
}

export class ApiError extends Error {
  constructor(readonly code: string, message: string) {
    super(message)
  }
}

let connection: Connection | null = null

export async function connect(): Promise<Connection> {
  connection = await invoke<Connection>('connection')
  return connection
}

/** Point the client at a restarted backend — new port, new token. */
export function reconnect(next: Connection) {
  connection = next
}

function required(): Connection {
  if (!connection) throw new ApiError('not_connected', '尚未连接到后端')
  return connection
}

async function request<T>(path: string, init: RequestInit = {}): Promise<T> {
  const { base_url, token } = required()
  const response = await fetch(`${base_url}${path}`, {
    ...init,
    headers: {
      Authorization: `Bearer ${token}`,
      ...(init.body instanceof FormData ? {} : { 'Content-Type': 'application/json' }),
      ...(init.headers ?? {}),
    },
  })
  if (!response.ok) throw await asError(response)
  if (response.status === 204) return undefined as T
  return (await response.json()) as T
}

async function asError(response: Response): Promise<ApiError> {
  let body: unknown = null
  try {
    body = await response.json()
  } catch {
    return new ApiError('http_error', `请求失败（HTTP ${response.status}）`)
  }
  // Unwrap FastAPI's envelope, then read whichever shape is inside.
  const record = body as Record<string, unknown>
  const inner = (record?.detail ?? record) as Record<string, unknown>
  const code = typeof inner?.code === 'string' ? inner.code : 'http_error'
  const message =
    typeof inner?.message === 'string' ? inner.message : `请求失败（HTTP ${response.status}）`
  return new ApiError(code, message)
}

// -- the flow --------------------------------------------------------------

export async function uploadSource(file: File): Promise<string> {
  const form = new FormData()
  form.append('file', file)
  const body = await request<{ upload_id: string }>('/uploads', { method: 'POST', body: form })
  return body.upload_id
}

/** Parse a deck and stop — fast, and everything the script needs to be written. */
export async function prepare(uploadId: string, brief: string) {
  return request<{ project_id: string; title: string; pages: PageView[] }>('/agent/prepare', {
    method: 'POST',
    body: JSON.stringify({ upload_id: uploadId, brief }),
  })
}

export async function narrationGuide(projectId: string) {
  const body = await request<{ items: GuideRow[] }>(`/projects/${projectId}/narration-guide`)
  return body.items
}

export async function submitNarrations(projectId: string, narrations: Record<string, string>) {
  return request<{ job_id: string }>(`/projects/${projectId}/narrations`, {
    method: 'POST',
    body: JSON.stringify({ narrations }),
  })
}

export async function reviseScene(projectId: string, sceneId: string, narration: string) {
  return request<{ job_id: string }>(`/projects/${projectId}/scenes/${sceneId}/narration`, {
    method: 'POST',
    body: JSON.stringify({ narration }),
  })
}

/**
 * Say something about an existing project and let the planner work out what to
 * do — the route this API was built around: one message handles creation and
 * every later edit. It understands page references, durations, and asks to
 * re-voice or re-direct; anything it cannot map confidently it treats as the
 * cheapest safe change rather than rewriting the video.
 */
export async function runAgent(projectId: string, message: string) {
  return request<{ job_id: string }>('/agent/run', {
    method: 'POST',
    body: JSON.stringify({ project_id: projectId, message }),
  })
}

export async function scenes(projectId: string) {
  const body = await request<{ items: Scene[] }>(`/projects/${projectId}/scenes`)
  return body.items
}

export async function quality(projectId: string) {
  return request<Quality>(`/projects/${projectId}/quality`)
}

export async function capabilities() {
  return request<{
    llm: { provider: string; model: string; available: boolean; configured: string }
    tts: { provider: string }
    renderers: Record<string, { available: boolean; reason: string }>
  }>('/health/capabilities')
}

/**
 * A project asset — page renders, audio clips — as a URL the page can load.
 *
 * `relative` is project-relative and already starts with its own directory
 * (`assets/page_001.png`), so the route's own `/assets/` segment is a second
 * one. Collapsing them 404s.
 */
export function assetUrl(projectId: string, relative: string): string {
  const { base_url, token } = required()
  const path = `/projects/${projectId}/assets/${relative}`
  return `${base_url}${path}?token=${encodeURIComponent(token)}`
}

export function videoUrl(projectId: string): string {
  const { base_url, token } = required()
  // The <video> element cannot send a header, so the token rides in the query.
  return `${base_url}/projects/${projectId}/video?token=${encodeURIComponent(token)}`
}

// -- progress --------------------------------------------------------------

/**
 * Follow one job to its end, calling `onState` for every update.
 *
 * Resolves with the terminal state. Rejects only if the stream itself fails —
 * a job that *fails* still resolves, because "it failed" is an outcome the UI
 * has to render rather than an error in getting the news.
 */
export async function watchJob(
  jobId: string,
  onState: (state: JobState) => void,
  signal?: AbortSignal,
): Promise<JobState> {
  const { base_url, token } = required()
  const response = await fetch(`${base_url}/jobs/${jobId}/events`, {
    headers: { Authorization: `Bearer ${token}` },
    signal,
  })
  if (!response.ok || !response.body) throw await asError(response)

  const reader = response.body.getReader()
  const decoder = new TextDecoder()
  let buffer = ''
  let last: JobState | null = null

  while (true) {
    const { done, value } = await reader.read()
    if (done) break
    buffer += decoder.decode(value, { stream: true })

    // SSE frames are separated by a blank line; anything after the last one is
    // a partial frame and stays in the buffer.
    const frames = buffer.split('\n\n')
    buffer = frames.pop() ?? ''
    for (const frame of frames) {
      if (frame.startsWith(':')) continue // keep-alive
      if (frame.includes('event: done')) return last ?? (await jobState(jobId))
      const line = frame.split('\n').find((l) => l.startsWith('data: '))
      if (!line) continue
      last = JSON.parse(line.slice(6)) as JobState
      onState(last)
    }
  }
  return last ?? (await jobState(jobId))
}

export async function jobState(jobId: string) {
  return request<JobState>(`/jobs/${jobId}`)
}

// -- settings --------------------------------------------------------------

export interface ProviderInfo {
  id: string
  label: string
  needs_key: boolean
  needs_base_url: boolean
  note?: string
  /** What the second field is called — for the local CLI it is not a model. */
  model_label?: string
}

export interface ModelInfo {
  id: string
  label: string
  vision: boolean
  note: string
}

/** Which providers this build can reach, and a starting list of model ids. */
export async function catalogue() {
  return request<{ providers: ProviderInfo[]; models: Record<string, ModelInfo[]> }>(
    '/health/models',
  )
}

export interface RuntimeStatus {
  ready: boolean
  installed: string | null
  required: string
  target: string
  approx_mb: number
}

/** Whether the part of the app that does the work is installed yet. */
export async function runtimeStatus(): Promise<RuntimeStatus> {
  return invoke<RuntimeStatus>('runtime_status')
}

/** Download and install it, then connect to the backend it brings. */
export async function installRuntime(): Promise<Connection> {
  const next = await invoke<Connection>('install_runtime')
  reconnect(next)
  return next
}

export interface ModelPrefs {
  provider: string
  model: string
  base_url: string
}

export async function modelPrefs(): Promise<ModelPrefs> {
  return invoke<ModelPrefs>('model_prefs')
}

/** Choosing a model restarts the backend, which is why this returns a connection. */
export async function saveModelPrefs(prefs: ModelPrefs): Promise<Connection> {
  const next = await invoke<Connection>('save_model_prefs', { prefs })
  reconnect(next)
  return next
}

export async function configuredKeys(): Promise<string[]> {
  return invoke<string[]>('configured_keys')
}

/** Storing a key restarts the backend, which is why this returns a connection. */
export async function saveKey(vendor: string, key: string): Promise<Connection> {
  const next = await invoke<Connection>('save_key', { vendor, key })
  reconnect(next)
  return next
}
