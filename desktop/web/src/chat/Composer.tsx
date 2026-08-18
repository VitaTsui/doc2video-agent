/**
 * The input line — the component library's, not ours.
 *
 * `Chat.Input` already is what this needs: a message box, file attachment, a
 * busy/stop state, and a model selector built into the same row. Hand-rolling
 * those was a mistake worth undoing — the picker in particular, which is the
 * whole reason this file used to be two files.
 *
 * The transcript above is still ours. `Chat.List` renders an assistant turn as
 * markdown with no escape hatch for arbitrary React (`userRenderContent` exists
 * only for the user's side), and every interesting turn here is interactive: a
 * per-page script editor with a button that starts a render, a live progress
 * bar, a video player. Those cannot survive being flattened into a string.
 */

// The input alone, not the Chat barrel. Reaching one level up pulls ChatList
// with it, and ChatList's markdown renderer drags in mermaid, cytoscape and
// pdf.js — six megabytes of diagram engines for a text box.
import ChatInput from '@hsu-react/ui/es/components/Chat/ChatInput'
import type { ModelConfig } from '@hsu-react/ui/es/components/Chat/ChatInput'
import type { UploadFile } from 'antd'
import { useState } from 'react'

export interface ModelChoice {
  /** `provider/model`, or empty for "no model". */
  value: string
  label: string
}

export interface ModelGroup {
  label: string
  models: ModelChoice[]
}

export function Composer({
  disabled,
  hint,
  groups,
  model,
  onModel,
  onSend,
  onDeck,
}: {
  disabled: boolean
  hint: string
  groups: ModelGroup[]
  model: string
  onModel: (value: string) => void
  onSend: (text: string) => void | Promise<void>
  onDeck: (file: File, brief: string) => void | Promise<void>
}) {
  const [files, setFiles] = useState<UploadFile[]>([])

  // antd's Select reads an entry with an `options` array as a group heading,
  // and the library hands `modelList` straight through to it. The declared
  // type is a flat list — it has an index signature, so a group fits, but the
  // cast is what says so out loud. Grouping is worth it: "runs on this machine,
  // costs nothing" and "calls an API you pay for" is the distinction that
  // actually decides which of these a person wants.
  const modelList = groups.map((group) => ({
    label: group.label,
    options: group.models.map((choice) => ({ label: choice.label, value: choice.value })),
  })) as unknown as ModelConfig[]

  return (
    <div className="composer">
      <div className="column">
        <ChatInput
          placeholder={hint}
          assistanting={disabled}
          fileList={files}
          onFileListChange={setFiles}
          uploadConfig={{ accept: '.pdf,.ppt,.pptx' }}
          modelConfig={{ modelList, modelType: model, setModelType: onModel }}
          onSend={(text) => {
            // A deck and the sentence describing what to do with it arrive in
            // the same turn: the file is held here until send, not uploaded on
            // pick, so the brief can still be typed after choosing it.
            // One deck per turn; a second attachment would have nowhere to go.
            const picked = files[0]?.originFileObj as File | undefined
            if (picked) {
              void onDeck(picked, text.trim())
              setFiles([])
              return
            }
            if (text.trim()) void onSend(text.trim())
          }}
        />
      </div>
    </div>
  )
}
