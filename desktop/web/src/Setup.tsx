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
  const [progress, setProgress] = useState<{
    phase: 'download' | 'unpack'
    done: number
    total: number
  } | null>(null)
  const [error, setError] = useState<string | null>(null)

  useEffect(() => {
    const stop = listen<['download' | 'unpack', number, number]>(
      'runtime://progress',
      (event) => {
        const [phase, done, total] = event.payload
        setProgress({ phase, done, total })
      },
    )
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
  // Two phases, said separately. Unpacking twenty thousand files takes minutes
  // on a machine whose antivirus opens every one of them, and it used to
  // happen with the bar parked at 100% — which reads as a crash, not as work.
  const unpacking = progress?.phase === 'unpack'

  return (
    <div className="shell">
      <div className="transcript">
        <div className="column" style={{ maxWidth: '34rem', paddingTop: 64 }}>
          <h2 style={{ marginTop: 0 }}>{status.needs_base ? '先装一次运行环境' : '更新运行环境'}</h2>
          {/* Two different sentences because they are two different waits: the
              first install is four hundred megabytes and twenty thousand
              files, an ordinary update is a fifth of a megabyte. A screen that
              said "约 400MB" for both taught people to expect the worse one. */}
          {status.needs_base ? (
            <p>
              应用本体只有几兆，真正干活的东西还没下载：Python 运行时和整条流水线、
              ffmpeg、中文语音模型、中文字体，一共约 {status.approx_mb}MB。
              这一步只有第一次、以及依赖变化时才需要；平时的版本更新只下几百 KB。
            </p>
          ) : (
            <p>
              依赖没有变化，只需要更新流水线本身，约 {status.approx_mb}MB，几秒钟。
            </p>
          )}
          {status.installed && (
            <p className="muted">
              当前装的是 {status.installed}，这个版本需要 {status.required}。
            </p>
          )}

          {error && (
            <div className="card" style={{ color: '#b0562f' }}>
              {error}
              {/* The guess this used to make ("可能还没发布") read as a
                  diagnosis and was usually wrong — the message above now
                  carries the actual cause. What belongs here is where to look
                  when it still is not enough. */}
              <div className="muted" style={{ marginTop: 6 }}>
                每次尝试的断点和错误都记在应用数据目录的 runtime-install.log 里。
              </div>
            </div>
          )}

          {installing ? (
            <div className="card">
              <div style={{ display: 'flex', justifyContent: 'space-between', marginBottom: 8 }}>
                <span>{unpacking ? '正在解压（文件很多，会慢一些）' : '正在下载运行环境'}</span>
                <span className="muted">
                  {unpacking || percent !== null ? `${percent ?? 0}%` : `${downloadedMb}MB`}
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
                {unpacking
                  ? '两万多个小文件，Windows 上杀毒软件会逐个扫描，这一步比下载还久。'
                  : '下载完会校验完整性再解压，中途关掉不会损坏已有环境。'}
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
