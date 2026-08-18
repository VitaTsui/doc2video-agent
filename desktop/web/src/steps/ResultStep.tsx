/** Step four: watch it, read the quality report, fix one page if needed. */

import { useEffect, useState } from 'react'
import { Alert, Button, Card, Descriptions, Input, List, Progress, Space, Typography } from 'antd'

import * as api from '../api'
import type { Quality, Scene } from '../api'

export function ResultStep({
  projectId,
  scenes,
  onRevised,
  onRestart,
}: {
  projectId: string
  scenes: Scene[]
  onRevised: (jobId: string) => Promise<void>
  onRestart: () => void
}) {
  const [quality, setQuality] = useState<Quality | null>(null)
  const [editing, setEditing] = useState<string | null>(null)
  const [draft, setDraft] = useState('')

  useEffect(() => {
    // A project is only scored after review; a 404 here just means not yet.
    api.quality(projectId).then(setQuality).catch(() => setQuality(null))
  }, [projectId])

  async function revise(sceneId: string) {
    const { job_id } = await api.reviseScene(projectId, sceneId, draft)
    setEditing(null)
    await onRevised(job_id)
  }

  return (
    <Space direction="vertical" size="middle" style={{ width: '100%' }}>
      <Card>
        <video
          src={api.videoUrl(projectId)}
          controls
          style={{ width: '100%', maxHeight: 420, background: '#000' }}
        />
      </Card>

      {quality && (
        <Card size="small" title={`质量分 ${quality.score}`}>
          <Descriptions size="small" column={1}>
            {quality.dimensions.map((d) => (
              <Descriptions.Item key={d.name} label={d.name}>
                <Space>
                  <Progress
                    percent={Math.round(d.score)}
                    size="small"
                    style={{ width: 160 }}
                    showInfo={false}
                  />
                  <Typography.Text type="secondary">{d.detail}</Typography.Text>
                </Space>
              </Descriptions.Item>
            ))}
          </Descriptions>
        </Card>
      )}

      <Alert
        type="info"
        showIcon
        message="改一页只重做那一页"
        description="改动后只有这一页会重新配音和渲染，其余片段直接复用。"
      />

      <List
        bordered
        dataSource={scenes}
        renderItem={(scene) => (
          <List.Item
            actions={[
              editing === scene.scene_id ? (
                <Button type="link" onClick={() => revise(scene.scene_id)}>
                  重做这一页
                </Button>
              ) : (
                <Button
                  type="link"
                  onClick={() => {
                    setEditing(scene.scene_id)
                    setDraft(scene.narration)
                  }}
                >
                  改
                </Button>
              ),
            ]}
          >
            <List.Item.Meta
              title={`第 ${scene.source_page} 页 · ${scene.duration.toFixed(1)}s`}
              description={
                editing === scene.scene_id ? (
                  <Input.TextArea
                    rows={3}
                    value={draft}
                    onChange={(e) => setDraft(e.target.value)}
                  />
                ) : (
                  scene.narration
                )
              }
            />
          </List.Item>
        )}
      />

      <Button onClick={onRestart}>再做一支</Button>
    </Space>
  )
}
