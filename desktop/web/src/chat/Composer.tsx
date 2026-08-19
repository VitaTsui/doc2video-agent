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
  uploadAction,
}: {
  disabled: boolean
  hint: string
  groups: ModelGroup[]
  model: string
  onModel: (value: string) => void
  onSend: (text: string) => void | Promise<void>
  onDeck: (file: File, brief: string, uploadId?: string) => void | Promise<void>
  /** Where the picker posts. Carries the token, which it cannot send as a header. */
  uploadAction: string
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
          // A real address, not just an accept filter: without one the picker
          // has nowhere to post and says so, in the middle of the composer.
          // The upload then happens while the brief is still being typed,
          // which is the better order anyway — a 6MB deck is already on the
          // backend by the time the sentence is finished.
          uploadConfig={{ accept: '.pdf,.ppt,.pptx', action: uploadAction }}
          modelConfig={{ modelList, modelType: model, setModelType: onModel }}
          onSend={(text) => {
            // A deck and the sentence describing what to do with it arrive in
            // the same turn: the file is held here until send, not uploaded on
            // pick, so the brief can still be typed after choosing it.
            // One deck per turn; a second attachment would have nowhere to go.
            const attached = files[0]
            const picked = attached?.originFileObj as File | undefined
            if (picked) {
              // The component already uploaded it; its answer carries the id.
              // Falling back to the File covers an upload that failed, so a
              // deck is never lost to a transient error.
              const uploaded = (attached?.response as { upload_id?: string } | undefined)?.upload_id
              void onDeck(picked, text.trim(), uploaded)
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
