/**
 * The doc2video backend, as something this process owns or merely borrows.
 *
 * Two modes, and the difference is who is responsible for the process:
 *
 * * **Borrowed** — `baseUrl` is configured, something else started the server,
 *   and disposal leaves it running. Also the only mode that can point at
 *   another machine.
 * * **Owned** — nothing is configured, so the plugin starts one: a free port
 *   chosen by binding zero, a token minted per launch and never written down,
 *   a storage directory of its own, and a kill on disposal. This is the mode
 *   that makes the plugin worth installing — the user runs `dsh`, not a
 *   server.
 *
 * @module dsh-plugin-doc2video/backend
 */

import { spawn } from 'node:child_process'
import type { ChildProcess } from 'node:child_process'
import { randomBytes } from 'node:crypto'
import { readFile } from 'node:fs/promises'
import { createServer } from 'node:net'
import { basename } from 'node:path'
import { setTimeout as delay } from 'node:timers/promises'

/** How the plugin was told to reach a backend. */
export interface BackendOptions {
  /** An already-running server. Empty means start one. */
  baseUrl: string
  /** Bearer token for a borrowed server; owned ones get a fresh one. */
  token: string
  command: string
  args: string[]
  cwd: string
  storageDir: string
  env: Record<string, string>
  startupTimeoutMs: number
  requestTimeoutMs: number
}

/** A structured error from the service, kept structured for the model. */
export class BackendError extends Error {
  constructor(readonly code: string, message: string, readonly status: number) {
    super(message)
    this.name = 'BackendError'
  }
}

/** One port nobody is listening on. Racy by nature; the server binds it next. */
async function freePort(): Promise<number> {
  return new Promise((resolve, reject) => {
    const probe = createServer()
    probe.once('error', reject)
    probe.listen(0, '127.0.0.1', () => {
      const address = probe.address()
      const port = typeof address === 'object' && address ? address.port : 0
      probe.close(() => (port ? resolve(port) : reject(new Error('no free port'))))
    })
  })
}

export class Backend {
  private child: ChildProcess | undefined
  private base = ''
  private token = ''
  /** Where an owned server keeps its projects, so outputs can be named by path. */
  private storage = ''

  constructor(private readonly options: BackendOptions) {}

  /** True once there is something to talk to. */
  get ready(): boolean {
    return this.base !== ''
  }

  get storageDir(): string {
    return this.storage
  }

  /**
   * Reach a backend: borrow the configured one, or start one and wait for it.
   * @returns the base URL now in use.
   */
  async open(): Promise<string> {
    if (this.options.baseUrl) {
      this.base = this.options.baseUrl.replace(/\/+$/, '')
      this.token = this.options.token
      await this.waitHealthy(Date.now() + this.options.startupTimeoutMs)
      return this.base
    }

    const port = await freePort()
    // Minted per launch and passed through the environment: a token on the
    // command line is a token in every process listing on the machine.
    this.token = this.options.token || randomBytes(32).toString('hex')
    this.storage = this.options.storageDir
    const child = spawn(
      this.options.command,
      [...this.options.args, 'serve', '--host', '127.0.0.1', '--port', String(port)],
      {
        cwd: this.options.cwd || process.cwd(),
        env: {
          ...process.env,
          ...this.options.env,
          D2V_API_TOKEN: this.token,
          ...this.storage ? { D2V_STORAGE_DIR: this.storage } : {},
        },
        stdio: ['ignore', 'pipe', 'pipe'],
      },
    )
    this.child = child
    this.base = `http://127.0.0.1:${port}`

    // Keep the last of what it said, so a startup failure can be reported as
    // the reason it gave rather than as a timeout.
    let tail = ''
    const remember = (chunk: Buffer): void => {
      tail = `${tail}${chunk.toString()}`.slice(-2000)
    }
    child.stdout?.on('data', remember)
    child.stderr?.on('data', remember)

    let died: string | undefined
    child.once('exit', (code, signal) => {
      died = `退出（code=${code ?? '-'}, signal=${signal ?? '-'}）`
    })

    const deadline = Date.now() + this.options.startupTimeoutMs
    while (Date.now() < deadline) {
      if (died !== undefined) {
        throw new Error(`doc2video 后端启动失败：${died}\n${tail.trim()}`)
      }
      if (await this.healthy()) return this.base
      await delay(300)
    }
    this.close()
    throw new Error(
      `doc2video 后端 ${this.options.startupTimeoutMs}ms 内没有就绪（${this.options.command}）\n${tail.trim()}`,
    )
  }

  /** Stop an owned server. A borrowed one is left alone. */
  close(): void {
    const child = this.child
    this.child = undefined
    this.base = ''
    if (!child || child.exitCode !== null) return
    child.kill('SIGTERM')
    // A render holds ffmpeg open; give it a moment before insisting.
    const hard = setTimeout(() => child.kill('SIGKILL'), 5000)
    child.once('exit', () => clearTimeout(hard))
    hard.unref?.()
  }

  private async healthy(): Promise<boolean> {
    try {
      const response = await fetch(`${this.base}/health`, { signal: AbortSignal.timeout(2000) })
      return response.ok
    } catch {
      return false
    }
  }

  private async waitHealthy(deadline: number): Promise<void> {
    while (Date.now() < deadline) {
      if (await this.healthy()) return
      await delay(300)
    }
    throw new Error(`连不上 doc2video 后端：${this.base}`)
  }

  /**
   * One request, with the service's own error shape preserved.
   *
   * The service answers failures two ways — a bare `{code, message}` and one
   * wrapped in FastAPI's `detail` — so both are unwrapped here rather than at
   * every call site.
   */
  async request<T>(path: string, init: RequestInit = {}, signal?: AbortSignal): Promise<T> {
    if (!this.base) throw new Error('doc2video 后端尚未就绪')
    const headers = new Headers(init.headers)
    if (this.token) headers.set('Authorization', `Bearer ${this.token}`)
    const response = await fetch(`${this.base}${path}`, {
      ...init,
      headers,
      signal: signal ?? AbortSignal.timeout(this.options.requestTimeoutMs),
    })
    const text = await response.text()
    const body: unknown = text ? JSON.parse(text) : {}
    if (!response.ok) {
      const detail = (body as { detail?: unknown }).detail
      const shape = (detail && typeof detail === 'object' ? detail : body) as {
        code?: string
        message?: string
      }
      throw new BackendError(
        shape.code ?? 'http_error',
        shape.message ?? `${response.status} ${response.statusText}`,
        response.status,
      )
    }
    return body as T
  }

  async json<T>(path: string, payload: unknown, signal?: AbortSignal): Promise<T> {
    return this.request<T>(
      path,
      {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(payload),
      },
      signal,
    )
  }

  /**
   * Send a file the model named by path.
   *
   * This is the whole reason for a plugin rather than the MCP bridge: over MCP
   * a source file has to arrive base64-encoded inside a tool call, which a
   * four-megabyte deck cannot survive. dsh runs on the same machine as the
   * file, so the path means something and the bytes never enter the
   * conversation.
   */
  async upload(path: string, signal?: AbortSignal): Promise<{ upload_id: string }> {
    const bytes = await readFile(path)
    const form = new FormData()
    form.append('file', new Blob([new Uint8Array(bytes)]), basename(path))
    return this.request<{ upload_id: string }>(
      '/uploads',
      { method: 'POST', body: form },
      signal,
    )
  }
}
