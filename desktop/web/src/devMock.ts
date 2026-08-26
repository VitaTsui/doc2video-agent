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

const GUIDE = [
  { page: 1, page_type: 'cover', target_seconds: 24, target_chars: 105, page_seconds: 24 },
  { page: 2, page_type: 'agenda', target_seconds: 29, target_chars: 135, page_seconds: 29 },
]

const SCENES = [
  { scene_id: 'scene_01', source_page: 1, title: '封面', duration: 24, narration: '各位评审专家，下面汇报的是面向石化化工方向的应用场景揭榜方案。', actions: [] },
  { scene_id: 'scene_02', source_page: 2, title: '目录', duration: 29, narration: '汇报分五个部分。先交代这次揭榜的背景，以及我们作为技术牵头方的能力来源。', actions: [] },
  { scene_id: 'scene_03', source_page: 3, title: '一、背景', duration: 20, narration: '先看背景和技术牵头方。这一部分回答两个问题。', actions: [] },
]

const QUALITY = {
  score: 100,
  errors: 0,
  warnings: 0,
  dimensions: [
    { name: 'completeness', score: 100, weight: 0.35, detail: '0 处不完整' },
    { name: 'pacing', score: 100, weight: 0.2, detail: '节奏均匀' },
    { name: 'grounding', score: 100, weight: 0.2, detail: '没有脱离页面内容的场景' },
  ],
}

const LEDGER = [
  { seq: 1, kind: 'stage', name: '解析文档', detail: '', status: 'ok', duration_s: 2.3, artifacts: [], run_id: 'r1' },
  { seq: 2, kind: 'decision', name: '决定写讲稿', detail: '还没有讲稿，先按页写一版', status: 'ok', duration_s: 0, artifacts: [], run_id: 'r1' },
  { seq: 3, kind: 'stage', name: '渲染合成', detail: '', status: 'ok', duration_s: 263, artifacts: [], run_id: 'r1' },
]

const SESSION = [
  { speaker: 'user', text: '给评审专家讲这份揭榜方案，六分钟左右。', action: '' },
  { speaker: 'agent', text: '不确定受众偏技术还是偏业务，先按评审专家来写。', action: 'ask' },
  { speaker: 'agent', text: '成片已完成，共 30 页、约 7 分钟，质检 100 分。', action: '' },
]

const PREFS: ModelPrefs = {
  providers: [
    {
      id: 'p_ds',
      name: 'DeepSeek',
      protocol: 'compatible',
      base_url: 'https://api.deepseek.com/v1',
      models: [
        { id: 'deepseek-chat', name: 'DeepSeek-V4-Flash', note: '快，日常够用' },
        { id: 'deepseek-reasoner', name: 'DeepSeek-V4-Pro', note: '会推理，长稿更稳' },
      ],
    },
    {
      id: 'p_cli',
      name: '本机 CLI',
      protocol: 'agent_cli',
      base_url: '',
      models: [
        { id: 'codex', name: 'Codex CLI', note: '用这台机器上装好的 codex，不消耗 API 额度' },
        { id: 'claude-code', name: 'Claude Code CLI', note: '用这台机器上装好的 claude，不消耗 API 额度' },
      ],
    },
  ],
  active: 'p_cli',
  active_model: 'claude-code',
}

const CATALOGUE = {
  providers: [
    { id: 'agent_cli', label: '本机 CLI Agent', needs_key: false, needs_base_url: false },
    { id: 'anthropic', label: 'Anthropic', needs_key: true, needs_base_url: false },
  ],
  models: {
    agent_cli: [
      { id: 'claude-code', label: 'Claude Code', vision: false, note: '检测到 /usr/local/bin/claude', installed: true },
      { id: 'codex', label: 'Codex', vision: false, note: '未检测到 codex', installed: false },
    ],
    anthropic: [
      { id: 'claude-opus-5', label: 'Claude Opus 5', vision: true, note: '' },
      { id: 'claude-sonnet-5', label: 'Claude Sonnet 5', vision: true, note: '' },
    ],
  },
}

const CAPABILITIES = {
  llm: {
    provider: 'agent_virtualization',
    model: 'claude-code',
    available: true,
    configured: 'agent_cli',
    label: 'Claude Code',
  },
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
          // Point it at a running backend to look at real data instead of
          // canned answers — a mock only ever shows the shapes you thought of:
          //
          //   D2V_STORAGE_DIR=storage D2V_API_TOKEN=t D2V_PORT=8477 \
          //     D2V_CORS_ORIGINS='["http://localhost:5399"]' \
          //     uv run python -m doc2video.cli serve
          //   VITE_BACKEND=http://127.0.0.1:8477 VITE_TOKEN=t pnpm dev
          return import.meta.env.VITE_BACKEND
            ? { base_url: import.meta.env.VITE_BACKEND, token: import.meta.env.VITE_TOKEN ?? '' }
            : { base_url: 'http://127.0.0.1:1', token: 'dev' }
        case 'runtime_status':
          // The preview always has its runtime; the download screen is looked
          // at by pointing this at `ready: false`.
          return { ready: true, installed: '0.5.0', required: '0.5.0', target: 'macos-arm64', approx_mb: 400 }
        case 'model_prefs':
          return PREFS
        case 'save_model_prefs':
        case 'save_key':
          // Point it at a running backend to look at real data instead of
          // canned answers — a mock only ever shows the shapes you thought of:
          //
          //   D2V_STORAGE_DIR=storage D2V_API_TOKEN=t D2V_PORT=8477 \
          //     D2V_CORS_ORIGINS='["http://localhost:5399"]' \
          //     uv run python -m doc2video.cli serve
          //   VITE_BACKEND=http://127.0.0.1:8477 VITE_TOKEN=t pnpm dev
          return import.meta.env.VITE_BACKEND
            ? { base_url: import.meta.env.VITE_BACKEND, token: import.meta.env.VITE_TOKEN ?? '' }
            : { base_url: 'http://127.0.0.1:1', token: 'dev' }
        default:
          return null
      }
    },
  }

  // The backend is not there either; answer the handful of GETs the first
  // screen makes so the page reaches its resting state instead of an error.
  // With a real backend behind it, the canned answers step aside.
  if (import.meta.env.VITE_BACKEND) return

  const real = window.fetch.bind(window)
  window.fetch = async (input, init) => {
    const url = String(typeof input === 'string' ? input : (input as Request).url)
    if (url.includes('/health/models')) return json(CATALOGUE)
    if (url.includes('/health/capabilities')) return json(CAPABILITIES)
    // Enough of a history for the sidebar to have something to be: a list
    // styled only against the empty case is a list nobody has looked at.
    if (url.includes('/session')) return json({ items: SESSION, compacted: 0 })
    if (url.includes('/narration-guide')) return json({ items: GUIDE })
    if (url.includes('/scenes')) return json({ items: SCENES })
    if (url.includes('/quality')) return json(QUALITY)
    if (url.includes('/ledger')) return json({ items: LEDGER })
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
