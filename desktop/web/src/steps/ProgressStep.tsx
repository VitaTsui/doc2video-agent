/** Step three: what the pipeline is doing, and how far in it is. */

import { Card, Empty, Progress, Result, Tag, Timeline, Typography } from 'antd'

import type { JobState } from '../api'

/** The stages a full run walks through, in order. */
const STAGES: [string, string][] = [
  ['parse', '解析文档'],
  ['understand', '理解结构'],
  ['narrate', '生成讲稿'],
  ['voice', '配音'],
  ['direct', '镜头设计'],
  ['motion', '时间轴'],
  ['render', '渲染'],
  ['review', '质检'],
]

export function ProgressStep({ job }: { job: JobState | null }) {
  if (!job) return <Empty description="正在排队…" />

  if (job.status === 'failed') {
    return (
      <Result
        status="error"
        title="生成失败"
        subTitle={job.error?.message ?? job.detail}
        extra={<Typography.Text type="secondary">错误码：{job.error?.code}</Typography.Text>}
      />
    )
  }

  const reached = STAGES.findIndex(([key]) => key === job.stage)
  return (
    <Card>
      <Typography.Paragraph>
        <Typography.Text strong>{job.detail || '准备中'}</Typography.Text>
      </Typography.Paragraph>

      {/* A denominator only exists for the stages that loop over scenes; the
          rest would be a lie dressed as a percentage. */}
      {job.total > 0 ? (
        <Progress percent={Math.round((job.done / job.total) * 100)} />
      ) : (
        <Progress percent={100} status="active" showInfo={false} />
      )}

      <Timeline
        style={{ marginTop: 24 }}
        items={STAGES.map(([, label], index) => ({
          color: index < reached ? 'green' : index === reached ? 'blue' : 'gray',
          children: (
            <span>
              {label}
              {index === reached && job.total > 0 && (
                <Tag style={{ marginLeft: 8 }}>
                  {job.done}/{job.total}
                </Tag>
              )}
            </span>
          ),
        }))}
      />
    </Card>
  )
}
