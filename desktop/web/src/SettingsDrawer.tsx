/**
 * Model keys and what the backend can currently do.
 *
 * Keys are write-only from here: they go into the OS keychain and are never
 * read back into the page, so this shows "已配置" rather than the value. Saving
 * one restarts the backend — its settings are frozen for the life of the
 * process, so there is no way to apply a change without one.
 */

import { useEffect, useState } from 'react'
import { Alert, Button, Descriptions, Drawer, Form, Input, Space, Tag, message } from 'antd'

import * as api from './api'
import type { Connection } from './api'

const VENDORS: [string, string][] = [
  ['ANTHROPIC_API_KEY', 'Anthropic（Claude）'],
  ['OPENAI_API_KEY', 'OpenAI'],
  ['GEMINI_API_KEY', 'Google Gemini'],
  ['D2V_COMPATIBLE_API_KEY', 'OpenAI 兼容通道'],
]

export function SettingsDrawer({
  open,
  onClose,
  onReconnected,
}: {
  open: boolean
  onClose: () => void
  onReconnected: (connection: Connection) => void
}) {
  const [configured, setConfigured] = useState<string[]>([])
  const [capabilities, setCapabilities] = useState<Awaited<
    ReturnType<typeof api.capabilities>
  > | null>(null)
  const [saving, setSaving] = useState<string | null>(null)

  useEffect(() => {
    if (!open) return
    api.configuredKeys().then(setConfigured)
    api.capabilities().then(setCapabilities).catch(() => setCapabilities(null))
  }, [open])

  async function save(vendor: string, value: string) {
    setSaving(vendor)
    try {
      onReconnected(await api.saveKey(vendor, value))
      setConfigured(await api.configuredKeys())
      setCapabilities(await api.capabilities())
    } catch (e) {
      message.error((e as Error).message)
    } finally {
      setSaving(null)
    }
  }

  return (
    <Drawer title="设置" open={open} onClose={onClose} width={480}>
      <Alert
        type="info"
        showIcon
        style={{ marginBottom: 16 }}
        message="不配 Key 也能用"
        description="不配的话，讲稿由你自己写（或由调用方通过 MCP 传进来）。配了模型，留空的页会由模型代写。本机装了 Claude Code 或 Codex 的话，也可以不用 Key 直接用它们。"
      />

      {capabilities && (
        <Descriptions size="small" column={1} bordered style={{ marginBottom: 20 }}>
          <Descriptions.Item label="模型">
            {capabilities.llm.available ? (
              <Tag color="green">
                {capabilities.llm.provider}｜{capabilities.llm.model}
              </Tag>
            ) : (
              <Tag>未配置</Tag>
            )}
          </Descriptions.Item>
          <Descriptions.Item label="语音">{capabilities.tts.provider}</Descriptions.Item>
          <Descriptions.Item label="渲染器">
            {Object.entries(capabilities.renderers)
              .filter(([, info]) => info.available)
              .map(([name]) => (
                <Tag key={name}>{name}</Tag>
              ))}
          </Descriptions.Item>
        </Descriptions>
      )}

      <Form layout="vertical">
        {VENDORS.map(([vendor, label]) => (
          <Form.Item
            key={vendor}
            label={
              <Space>
                {label}
                {configured.includes(vendor) && <Tag color="green">已配置</Tag>}
              </Space>
            }
          >
            <Input.Search
              type="password"
              placeholder={configured.includes(vendor) ? '已保存，重新填写可覆盖' : '粘贴 API Key'}
              enterButton="保存"
              loading={saving === vendor}
              onSearch={(value) => save(vendor, value)}
            />
          </Form.Item>
        ))}
      </Form>

      <Button
        block
        onClick={async () => {
          const next = await api.saveKey('ANTHROPIC_API_KEY', '')
          onReconnected(next)
          setConfigured(await api.configuredKeys())
        }}
        style={{ display: 'none' }}
      />
    </Drawer>
  )
}
