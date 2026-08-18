/**
 * The whole flow, as four steps the user moves through in order.
 *
 * The order is not a UI preference — it is the pipeline's. A script cannot be
 * written before the deck is parsed (the per-page budget comes out of that),
 * and it cannot be changed after voicing without re-synthesising, because the
 * audio's length is what every later stage takes its timing from. So the
 * script step is a gate: everything before it is cheap and instant, everything
 * after it costs minutes.
 */

import { useCallback, useEffect, useRef, useState } from 'react'
import { App as AntApp, Button, ConfigProvider, Layout, Steps, Typography, message } from 'antd'
import zhCN from 'antd/locale/zh_CN'

import * as api from './api'
import type { Connection, GuideRow, JobState, PageView, Scene } from './api'
import { DropStep } from './steps/DropStep'
import { ScriptStep } from './steps/ScriptStep'
import { ProgressStep } from './steps/ProgressStep'
import { ResultStep } from './steps/ResultStep'
import { SettingsDrawer } from './SettingsDrawer'

export type Step = 'drop' | 'script' | 'progress' | 'result'

export function App() {
  const [connection, setConnection] = useState<Connection | null>(null)
  const [failure, setFailure] = useState<string | null>(null)
  const [step, setStep] = useState<Step>('drop')
  const [settingsOpen, setSettingsOpen] = useState(false)

  const [projectId, setProjectId] = useState<string | null>(null)
  const [pages, setPages] = useState<PageView[]>([])
  const [guide, setGuide] = useState<GuideRow[]>([])
  const [job, setJob] = useState<JobState | null>(null)
  const [scenes, setScenes] = useState<Scene[]>([])

  const abort = useRef<AbortController | null>(null)

  useEffect(() => {
    api
      .connect()
      .then(setConnection)
      .catch((error: Error) => setFailure(error.message))
    return () => abort.current?.abort()
  }, [])

  const follow = useCallback(async (jobId: string) => {
    setStep('progress')
    abort.current?.abort()
    abort.current = new AbortController()
    const final = await api.watchJob(jobId, setJob, abort.current.signal)
    setJob(final)
    if (final.status === 'succeeded' && final.project_id) {
      setScenes(await api.scenes(final.project_id))
      setStep('result')
    }
  }, [])

  if (failure) {
    return (
      <Layout style={{ height: '100vh', padding: 48 }}>
        <Typography.Title level={4}>后端没能启动</Typography.Title>
        <Typography.Paragraph type="secondary">
          界面本身没问题，是它背后的处理进程起不来。下面是它退出前的输出：
        </Typography.Paragraph>
        <Typography.Paragraph>
          <pre style={{ whiteSpace: 'pre-wrap', fontSize: 12 }}>{failure}</pre>
        </Typography.Paragraph>
      </Layout>
    )
  }

  return (
    <ConfigProvider locale={zhCN}>
      <AntApp>
        <Layout style={{ height: '100vh' }}>
          <Layout.Header
            style={{
              background: '#fff',
              borderBottom: '1px solid #f0f0f0',
              display: 'flex',
              alignItems: 'center',
              justifyContent: 'space-between',
              paddingInline: 24,
            }}
          >
            <Typography.Text strong style={{ fontSize: 16 }}>
              Doc2Video
            </Typography.Text>
            <Button type="text" onClick={() => setSettingsOpen(true)}>
              设置
            </Button>
          </Layout.Header>

          <Layout.Content style={{ padding: 24, overflow: 'auto' }}>
            <Steps
              size="small"
              current={['drop', 'script', 'progress', 'result'].indexOf(step)}
              style={{ marginBottom: 24 }}
              items={[
                { title: '投递文档' },
                { title: '确认讲稿' },
                { title: '生成中' },
                { title: '成片' },
              ]}
            />

            {step === 'drop' && (
              <DropStep
                ready={connection !== null}
                onPrepared={async (id, prepared) => {
                  setProjectId(id)
                  setPages(prepared)
                  setGuide(await api.narrationGuide(id))
                  setStep('script')
                }}
              />
            )}

            {step === 'script' && projectId && (
              <ScriptStep
                projectId={projectId}
                pages={pages}
                guide={guide}
                onSubmitted={follow}
              />
            )}

            {step === 'progress' && <ProgressStep job={job} />}

            {step === 'result' && projectId && (
              <ResultStep
                projectId={projectId}
                scenes={scenes}
                onRevised={follow}
                onRestart={() => {
                  setProjectId(null)
                  setPages([])
                  setJob(null)
                  setScenes([])
                  setStep('drop')
                }}
              />
            )}
          </Layout.Content>
        </Layout>

        <SettingsDrawer
          open={settingsOpen}
          onClose={() => setSettingsOpen(false)}
          onReconnected={(next) => {
            setConnection(next)
            message.success('已重启后端，新的设置已生效')
          }}
        />
      </AntApp>
    </ConfigProvider>
  )
}
