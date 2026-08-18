/**
 * The first launch, when there is nothing to run yet.
 *
 * The installer is a few megabytes; the interpreter, the pipeline, ffmpeg, the
 * voice and the font are four hundred more, and they arrive here. Showing this
 * as a screen rather than a silent wait is the whole point: four hundred
 * megabytes with no sign of movement is indistinguishable from a hang, and this
 * is the first thing anyone sees of the app.
 *
 * It also says what is being downloaded. "正在准备" tells someone nothing about
 * why their new 6MB app is pulling 400MB.
 */

import { useEffect, useState } from 'react'
import { listen } from '@tauri-apps/api/event'

import * as api from './api'
import type { RuntimeStatus } from './api'

export function Setup({
  status,
  onReady,
}: {
  status: RuntimeStatus
  onReady: () => void
}) {
  const [installing, setInstalling] = useState(false)
  const [progress, setProgress] = useState<{ done: number; total: number } | null>(null)
  const [error, setError] = useState<string | null>(null)

  useEffect(() => {
    const stop = listen<[number, number]>('runtime://progress', (event) => {
      const [done, total] = event.payload
      setProgress({ done, total })
    })
    return () => {
      void stop.then((off) => off())
    }
  }, [])

  async function install() {
    setInstalling(true)
    setError(null)
    try {
      await api.installRuntime()
      onReady()
    } catch (e) {
      setError(api.describeError(e))
      setInstalling(false)
    }
  }

  const percent =
    progress && progress.total > 0 ? Math.round((progress.done / progress.total) * 100) : null
  const downloadedMb = progress ? Math.round(progress.done / 1024 / 1024) : 0

  return (
    <div className="shell">
      <div className="transcript">
        <div className="column" style={{ maxWidth: '34rem', paddingTop: 64 }}>
          <h2 style={{ marginTop: 0 }}>先装一次运行环境</h2>
          <p>
            应用本体只有几兆，真正干活的东西还没下载：Python 运行时和整条流水线、
            ffmpeg、中文语音模型、中文字体，一共约 {status.approx_mb}MB。装一次，
            之后升级只会重新下载几兆的应用本体。
          </p>
          {status.installed && (
            <p className="muted">
              当前装的是 {status.installed}，这个版本需要 {status.required}。
            </p>
          )}

          {error && (
            <div className="card" style={{ color: '#b0562f' }}>
              {error}
              <div className="muted" style={{ marginTop: 6 }}>
                网络不通、或者这个版本还没发布对应平台（{status.target}）的运行时，都会是这个结果。
              </div>
            </div>
          )}

          {installing ? (
            <div className="card">
              <div style={{ display: 'flex', justifyContent: 'space-between', marginBottom: 8 }}>
                <span>正在下载运行环境</span>
                <span className="muted">
                  {percent === null ? `${downloadedMb}MB` : `${percent}%`}
                </span>
              </div>
              <div className="bar">
                {/* The server does not always say how big it is; a bar that
                    invents a percentage would be worse than one that sweeps. */}
                {percent === null ? (
                  <div className="bar__fill bar__fill--pulse" />
                ) : (
                  <div className="bar__fill" style={{ width: `${percent}%` }} />
                )}
              </div>
              <div className="muted" style={{ marginTop: 8 }}>
                下载完会校验完整性再解压，中途关掉不会损坏已有环境。
              </div>
            </div>
          ) : (
            <button
              type="button"
              className="composer__send"
              style={{ width: 'auto', height: 36, padding: '0 20px', borderRadius: 8 }}
              onClick={() => void install()}
            >
              下载并安装
            </button>
          )}
        </div>
      </div>
    </div>
  )
}
