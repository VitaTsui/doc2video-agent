/** Step one: hand over a deck and say what the video is for. */

import { useState } from 'react'
import { InboxOutlined } from '@ant-design/icons'
import { Alert, Card, Input, Spin, Typography, Upload } from 'antd'

import * as api from '../api'
import type { PageView } from '../api'

export function DropStep({
  ready,
  onPrepared,
}: {
  ready: boolean
  onPrepared: (projectId: string, pages: PageView[]) => Promise<void>
}) {
  const [brief, setBrief] = useState('')
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)

  async function accept(file: File) {
    setBusy(true)
    setError(null)
    try {
      const uploadId = await api.uploadSource(file)
      const prepared = await api.prepare(uploadId, brief)
      await onPrepared(prepared.project_id, prepared.pages)
    } catch (e) {
      setError((e as Error).message)
    } finally {
      setBusy(false)
    }
  }

  return (
    <Card>
      <Typography.Paragraph type="secondary">
        一句话说明这支视频给谁看、多长、哪几页要重点讲。留空也可以，会按默认来。
      </Typography.Paragraph>
      <Input
        placeholder="例如：8 分钟的产品讲解视频，面向企业客户，第 5~8 页重点讲"
        value={brief}
        onChange={(e) => setBrief(e.target.value)}
        style={{ marginBottom: 20 }}
        disabled={busy}
      />

      {error && <Alert type="error" message={error} style={{ marginBottom: 16 }} showIcon />}

      <Spin spinning={busy} tip="正在解析文档…">
        <Upload.Dragger
          accept=".pdf,.ppt,.pptx"
          multiple={false}
          disabled={!ready || busy}
          showUploadList={false}
          // Handled here rather than by antd: the file goes to the local
          // backend with a bearer token, not to an upload URL.
          beforeUpload={(file) => {
            void accept(file as unknown as File)
            return false
          }}
        >
          <p className="ant-upload-drag-icon">
            <InboxOutlined />
          </p>
          <p className="ant-upload-text">把 PDF / PPT / PPTX 拖到这里</p>
          <p className="ant-upload-hint">
            {ready ? '解析只要几秒，不会开始渲染' : '正在启动后端…'}
          </p>
        </Upload.Dragger>
      </Spin>
    </Card>
  )
}
