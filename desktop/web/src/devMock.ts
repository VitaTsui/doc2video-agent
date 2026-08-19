/**
 * Lets the window run in an ordinary browser, for looking at it.
 *
 * Everything the page needs from the shell arrives through Tauri's `invoke`,
 * which does not exist outside it — so opened in a browser the app stops at
 * "后端尚未启动" and there is nothing to see. That matters more than it sounds:
 * a control styled into invisibility, or one that hides itself when a request
 * fails, is only ever caught by looking, and the shell's window cannot be
 * screenshotted without the operating system's screen-recording permission.
 *
 * Only loaded in a dev build, and only when the shell is absent, so it can
 * never stand in for the real thing.
 */

import type { ModelPrefs } from './api'

interface TauriWindow {
  __TAURI_INTERNALS__?: { invoke: (cmd: string, args?: unknown) => Promise<unknown> }
}

const PROJECTS = [
  {
    project_id: 'proj_a',
    title: '石化AI商业情报中心-揭榜方案V1',
    source: '石化方案.pdf',
    status: 'completed',
    updated_at: new Date(Date.now() - 5 * 60_000).toISOString(),
    duration: 421,
    output: 'out/final.mp4',
  },
  {
    project_id: 'proj_b',
    title: '产品介绍',
    source: 'intro.pptx',
    status: 'reviewed',
    updated_at: new Date(Date.now() - 26 * 3600_000).toISOString(),
    duration: 180,
    output: 'out/final.mp4',
  },
  {
    project_id: 'proj_c',
    title: '',
    source: '未命名草稿.pptx',
    status: 'parsed',
    updated_at: new Date(Date.now() - 9 * 86_400_000).toISOString(),
    duration: 0,
    output: null,
  },
]

const SESSION = [
  { speaker: 'user', text: '给评审专家讲这份揭榜方案，六分钟左右。', action: '' },
  { speaker: 'agent', text: '不确定受众偏技术还是偏业务，先按评审专家来写。', action: 'ask' },
  { speaker: 'agent', text: '成片已完成，共 30 页、约 7 分钟，质检 100 分。', action: '' },
]

const PREFS: ModelPrefs = { provider: 'agent_cli', model: 'claude-code', base_url: '' }

const CATALOGUE = {
  providers: [
    { id: 'agent_cli', label: '本机 CLI Agent', needs_key: false, needs_base_url: false },
    { id: 'anthropic', label: 'Anthropic', needs_key: true, needs_base_url: false },
  ],
  models: {
    agent_cli: [
      { id: 'claude-code', label: 'Claude Code', vision: false, note: '' },
      { id: 'codex', label: 'Codex', vision: false, note: '' },
    ],
    anthropic: [
      { id: 'claude-opus-5', label: 'Claude Opus 5', vision: true, note: '' },
      { id: 'claude-sonnet-5', label: 'Claude Sonnet 5', vision: true, note: '' },
    ],
  },
}

const CAPABILITIES = {
  llm: { provider: 'agent_virtualization', model: 'claude-code', available: true, configured: 'agent_cli' },
  tts: { provider: 'macos_say' },
  renderers: { remotion: { available: true, reason: '' }, ffmpeg: { available: true, reason: '' } },
}

export function installDevMock() {
  const win = window as unknown as TauriWindow
  if (win.__TAURI_INTERNALS__) return // the real shell is here

  win.__TAURI_INTERNALS__ = {
    invoke: async (cmd) => {
      switch (cmd) {
        case 'connection':
          return { base_url: 'http://127.0.0.1:1', token: 'dev' }
        case 'runtime_status':
          // The preview always has its runtime; the download screen is looked
          // at by pointing this at `ready: false`.
          return { ready: true, installed: '0.5.0', required: '0.5.0', target: 'macos-arm64', approx_mb: 400 }
        case 'model_prefs':
          return PREFS
        case 'save_model_prefs':
        case 'save_key':
          return { base_url: 'http://127.0.0.1:1', token: 'dev' }
        case 'configured_keys':
          return []
        default:
          return null
      }
    },
  }

  // The backend is not there either; answer the handful of GETs the first
  // screen makes so the page reaches its resting state instead of an error.
  const real = window.fetch.bind(window)
  window.fetch = async (input, init) => {
    const url = String(typeof input === 'string' ? input : (input as Request).url)
    if (url.includes('/health/models')) return json(CATALOGUE)
    if (url.includes('/health/capabilities')) return json(CAPABILITIES)
    // Enough of a history for the sidebar to have something to be: a list
    // styled only against the empty case is a list nobody has looked at.
    if (url.includes('/session')) return json({ items: SESSION, compacted: 0 })
    if (url.match(/\/projects(\?|$)/)) return json({ items: PROJECTS })
    return real(input as RequestInfo, init)
  }
}

function json(body: unknown): Response {
  return new Response(JSON.stringify(body), {
    status: 200,
    headers: { 'Content-Type': 'application/json' },
  })
}
