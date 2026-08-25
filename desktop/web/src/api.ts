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
  status: 'queued' | 'running' | 'succeeded' | 'failed' | 'cancelled'
  /** Asked to stop; the stage in flight has not noticed yet. */
  stopping?: boolean
  stage: string
  detail: string
  done: number
  total: number
  project_id: string | null
  error: { code: string; message: string } | null
  /** What the agent said, for a chat turn. */
  reply: string
  result: { output_path: string | null } | null
}

export interface PageView {
  index: number
  title: string
  page_type: string
  summary: string
  /** Project-relative path to this page's render, e.g. "assets/page_001.png". */
  image: string | null
  /** The script this page already has — empty until something writes one. */
  narration: string
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
  /** This scene's own clip, when one has been rendered. */
  clip?: string | null
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
  /** The page it came off, where there is one. */
  page: number | null
}

export interface LedgerEntry {
  seq: number
  kind: 'stage' | 'call' | 'decision' | 'degradation' | 'note'
  name: string
  detail: string
  status: string
  duration_s: number
  artifacts: LedgerArtifact[]
  /** Which tools did the work: the parser, the voice, the renderer, the model. */
  tools: string[]
  /** The skill this step is the work of, e.g. `presentation-narration`. */
  skill: string
  /** For a call, the `seq` of the stage it happened inside. */
  parent: number
  /** For a call, what it was working on: `page:7`, `scene:scn_x`. Outputs are
   *  collected at the end of the stage; this is how each one finds its call. */
  covers: string[]
  /** Which run wrote this. The file keeps every run a project ever had. */
  run_id: string
}

/** One engine, as something to choose and possibly install. */
export interface VoicePack {
  /** How to add a voice of your own to this pack — different for each. */
  how: string
  /** The folder to put voice files in, for the one pack that takes files. */
  folder: string
  id: string
  name: string
  note: string
  /** Roughly what installing costs, in bytes. Zero for what is already there. */
  size: number
  /** Needs the network to speak. Worth knowing before choosing it. */
  online: boolean
  installed: boolean
  voices: { id: string; name: string; gender: string | null }[]
}

/** Every voice this machine can speak with, and what the rest would cost. */
export async function voicePacks() {
  return request<{
    /** What is configured, which is usually nothing. */
    current: string
    /** What that actually resolves to — the engine that would speak, and the
     *  voice it would use. Empty voice means the engine's own default. */
    provider: string
    /** Which pack that engine is, so it can be named the way it is named
     *  everywhere else rather than by its module name. */
    pack: string
    voice: string
    packs: VoicePack[]
  }>('/health/voices')
}

export interface Plugin {
  id: string
  name: string
  /** skill | parser | voice | renderer | model | binary */
  kind: string
  kind_name: string
  /** Which step of the pipeline it belongs to — an attribute, not a heading. */
  stage: string
  what: string
  available: boolean
  /** Why not, when it is not. */
  reason: string
  /** What it tells the model, verbatim. Empty when it asks none. */
  prompt: string
  /** Which prompt file that is, so it can be edited and put back. */
  prompt_id: string
  /** The text this build shipped with. */
  prompt_default: string
  prompt_edited: boolean
  /** Anything else worth reading: a path, a size, where it came from. */
  detail: Record<string, string>
  /** The numbers that decide what comes out, as they are set right now. */
  rules: {
    name: string
    /** As it reads, with its unit: 「0.55 秒」「340 字/分」. */
    value: string
    what: string
    /** The knob behind it, when it is one that can be changed. */
    id: string
    number: number
    default: number
    low: number
    high: number
    unit: string
    integer: boolean
  }[]
}

/** Change what a step says to the model. Empty text restores the shipped one. */
export async function setPrompt(id: string, text: string) {
  const body = await request<{ plugins: Plugin[] }>('/health/plugins/prompt', {
    method: 'PUT',
    body: JSON.stringify({ id, text }),
  })
  return body.plugins
}

/** Change one of those numbers. `null` puts the measured default back. */
export async function setRule(id: string, value: number | null) {
  const body = await request<{ plugins: Plugin[] }>('/health/plugins/rules', {
    method: 'PUT',
    body: JSON.stringify({ id, value }),
  })
  return body.plugins
}

/** Ask a running job to stop. It ends at the next thing it finishes. */
export async function cancelJob(jobId: string) {
  return request<JobState>(`/jobs/${jobId}/cancel`, { method: 'POST' })
}

/** Everything this build is made of, and what works on this machine. */
export async function plugins() {
  const body = await request<{ plugins: Plugin[] }>('/health/plugins')
  return body.plugins
}

export interface PiperVoice {
  key: string
  name: string
  quality: string
  language: string
  language_name: string
  language_english: string
  country: string
  /** The model's size in bytes, so the button can say what it costs. */
  size: number
  installed: boolean
}

/** The published Piper voices, searchable. 「中文」/`zh`/`Chinese` all match. */
export async function piperVoices(q = '', limit = 40) {
  const query = new URLSearchParams({ q, limit: String(limit) })
  return request<{ total: number; matched: number; voices: PiperVoice[] }>(
    `/health/voices/piper?${query}`,
  )
}

/** Download one, into the folder the provider reads. Tens of megabytes. */
export async function installPiperVoice(key: string) {
  return request<{ voices: PiperVoice[] }>('/health/voices/piper/install', {
    method: 'POST',
    body: JSON.stringify({ key }),
  })
}

/** Choose the voice new videos start with. Empty hands it back to the machine. */
export async function chooseVoice(voice: string) {
  return request<Awaited<ReturnType<typeof voicePacks>>>('/health/voices/current', {
    method: 'PUT',
    body: JSON.stringify({ voice }),
  })
}

/**
 * One sentence in this voice, as a URL an `<audio>` can play.
 *
 * A URL rather than a fetched blob: the app's CSP allows media from the
 * backend and not from `blob:`, so a blob plays in a browser and fails inside
 * the window it was built for. The token rides in the query for the same
 * reason it does for the finished video — a media element cannot send a header.
 */
export function previewVoiceUrl(voice: string): string {
  const { base_url, token } = required()
  const query = new URLSearchParams({ voice, token })
  return `${base_url}/health/voices/preview?${query}`
}

/** Put a pack into the runtime. Slow for the big one; it says its size first. */
export async function installVoicePack(pack: string) {
  return request<{ installed: boolean; voices: VoicePack['voices'] }>(
    '/health/voices/install',
    { method: 'POST', body: JSON.stringify({ pack }) },
  )
}

/** How this project got made, step by step, with what each step produced. */
export async function ledger(projectId: string) {
  const body = await request<{ items: LedgerEntry[] }>(`/projects/${projectId}/ledger`)
  return body.items
}

export interface ProjectSummary {
  project_id: string
  title: string
  source: string
  status: string
  updated_at: string | null
  duration: number
  output: string | null
}

/** Every project on this machine, most recently touched first. */
export async function projects() {
  const body = await request<{ items: ProjectSummary[] }>('/projects')
  return body.items
}

/**
 * Remove a project and everything it produced.
 *
 * Not the uploaded file: that lives under `uploads/` and the project only ever
 * held a copy, so deleting a video does not cost you the deck it came from.
 */
export async function deleteProject(projectId: string) {
  return request<{ deleted: string }>(`/projects/${projectId}`, { method: 'DELETE' })
}

export interface Turn {
  /** user | agent | tool | summary */
  speaker: string
  text: string
  /** Which of the loop's four operations this turn was, if any. */
  action: string
}

/**
 * What was said about this project last time.
 *
 * The transcript is written turn by turn beside the project, so it already
 * survived the process — until this route only the model could read it, which
 * left an agent that remembered the conversation talking to a window that had
 * forgotten it.
 */
export async function session(projectId: string) {
  return request<{ items: Turn[]; compacted: number }>(`/projects/${projectId}/session`)
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

/**
 * Where the file picker posts to.
 *
 * The component we use takes a URL and nothing else — no headers, no request
 * hook — so the token rides in the query, which the backend accepts for this
 * one route and for media. Without it the picker had no address at all and
 * said so, in the middle of the composer.
 */
export function uploadUrl(): string {
  const { base_url, token } = required()
  return `${base_url}/uploads?token=${encodeURIComponent(token)}`
}

export async function uploadSource(file: File): Promise<string> {
  const form = new FormData()
  form.append('file', file)
  const body = await request<{ upload_id: string }>('/uploads', { method: 'POST', body: form })
  return body.upload_id
}

/** A parsed project's pages, for one that was not parsed in this session. */
export async function pages(projectId: string) {
  const body = await request<{ items: PageView[] }>(`/projects/${projectId}/pages`)
  return body.items
}

/** Parse a deck and stop — fast, and everything the script needs to be written. */
export async function prepare(uploadId: string, brief: string) {
  return request<{
    project_id: string
    title: string
    pages: PageView[]
    /** Did the brief name a length, or is the number our default? */
    duration_stated: boolean
  }>('/agent/prepare', {
    method: 'POST',
    body: JSON.stringify({ upload_id: uploadId, brief }),
  })
}

/** Write a first script for a parsed deck. Returns a job: it takes a while,
 *  and each page is saved as it is written, so `pages` fills in as it goes.
 *
 *  `written` is what the user has already typed. Those pages come back
 *  unchanged; the model writes the rest and has to join onto them. */
export async function draftScript(projectId: string, written: Record<string, string> = {}) {
  const body = await request<{ job_id: string }>(`/projects/${projectId}/draft`, {
    method: 'POST',
    body: JSON.stringify({ narrations: written }),
  })
  return body.job_id
}

/**
 * Say the existing script again, and rebuild what depends on it.
 *
 * The words are not touched. The picture is: captions are drawn into the
 * frames and the camera moves are timed to sentence boundaries, so a clip of a
 * different length moves both.
 */
export async function revoice(projectId: string, voice = '', speechRate = 0) {
  const body = await request<{ job_id: string }>(`/projects/${projectId}/revoice`, {
    method: 'POST',
    body: JSON.stringify({ voice, speech_rate: speechRate }),
  })
  return body.job_id
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

/**
 * Say something and let the agent decide what to do about it.
 *
 * The route this replaced ran a regex over the message and a fixed pipeline
 * over whatever it guessed. This one hands the message to a model that can see
 * the deck, the current script and the last quality report — and can therefore
 * answer "第 3 页太长了" by actually rewriting that page.
 */
export async function chat(projectId: string, message: string) {
  return request<{ job_id: string }>(`/projects/${projectId}/chat`, {
    method: 'POST',
    body: JSON.stringify({ message }),
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
    /** `label` is the name it has in the settings panel; `provider` is the code path. */
    llm: {
      provider: string
      model: string
      available: boolean
      configured: string
      label?: string
      /** Why it is not the configured one. Empty when nothing went wrong. */
      reason?: string
    }
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
  /** Local CLIs only: whether the binary is actually on this machine. */
  installed?: boolean
}

/** Which providers this build can reach, and a starting list of model ids. */
export async function catalogue() {
  return request<{ providers: ProviderInfo[]; models: Record<string, ModelInfo[]> }>(
    '/health/models',
  )
}

export interface UpdateInfo {
  available: boolean
  version: string
  notes: string
  current: string
}

/**
 * Whether a newer shell exists. Never throws into the UI's path: no network,
 * a rate limit and a release without a manifest all mean "not now", and none
 * of them is worth interrupting someone over.
 */
export async function checkUpdate(): Promise<UpdateInfo | null> {
  return invoke<UpdateInfo>('check_update').catch(() => null)
}

/** Download it, replace this binary, and restart into the new one. */
export async function installUpdate(): Promise<void> {
  return invoke<void>('install_update')
}

export interface RuntimeStatus {
  ready: boolean
  installed: string | null
  required: string
  target: string
  /** How much *this* install downloads — 400MB for a base, ~2MB for an app. */
  approx_mb: number
  /** Whether the heavy half has to come down as well. */
  needs_base: boolean
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

/**
 * One model a provider offers.
 *
 * `id` goes on the wire, `name` is what the picker shows. Two fields because
 * they are for two readers: `deepseek-chat` is the API's word, "DeepSeek V4"
 * is the person's, and showing only the first makes you translate every time.
 */
export interface Model {
  id: string
  name: string
  /** One muted line under the name in the picker. */
  note: string
}

/** One configured way to reach models. */
export interface Provider {
  /** Stable; also its account in the keychain. */
  id: string
  /** Whatever the user calls it. Becomes a group heading in the picker. */
  name: string
  /** anthropic | openai | gemini | compatible | agent_cli */
  protocol: string
  base_url: string
  models: Model[]
}

export interface ModelPrefs {
  providers: Provider[]
  /** The provider that answers. Empty is a supported state: no model at all. */
  active: string
  /** And which of its models. Empty falls back to the provider's first. */
  active_model: string
}

/**
 * The four request shapes the pipeline implements, plus the local CLIs.
 *
 * This is the one part that cannot be typed in: they are different SDKs with
 * different request formats, structured-output support and image handling.
 * Everything else about a provider — its name, address and model id — is the
 * user's to write, because vendors and model ids move faster than releases do.
 */
export const PROTOCOLS: { id: string; label: string; note: string }[] = [
  { id: 'agent_cli', label: '本机 CLI', note: '用这台机器上装好的 Claude Code / Codex，不需要 Key' },
  { id: 'anthropic', label: 'Anthropic', note: 'api.anthropic.com 的原生格式' },
  { id: 'openai', label: 'OpenAI', note: 'api.openai.com 的原生格式' },
  { id: 'gemini', label: 'Google Gemini', note: '' },
  { id: 'compatible', label: 'OpenAI 兼容', note: 'DeepSeek、Kimi、通义、自建网关都走这条，必须填 Base URL' },
]

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
