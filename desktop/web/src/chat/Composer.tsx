/**
 * The input line — the component library's, not ours.
 *
 * `Chat.Input` already is what this needs: a message box, file attachment and
 * a busy/stop state. Its model selector is the one part we do not use: it is
 * an antd `Select` with no `optionRender`, so an option cannot carry a second
 * line, and a model is a name plus a line saying what it is for. Ours sits
 * above the box instead — see `ModelPicker`.
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
import type { UploadFile } from 'antd'
import { useState } from 'react'

import * as api from '../api'
import { ModelPicker } from '../ModelPicker'

export function Composer({
  disabled,
  hint,
  prefs,
  onPick,
  onSend,
  onDeck,
  uploadAction,
}: {
  disabled: boolean
  hint: string
  prefs: api.ModelPrefs
  onPick: (providerId: string, modelId: string) => void
  onSend: (text: string) => void | Promise<void>
  onDeck: (file: File, brief: string, uploadId?: string) => void | Promise<void>
  /** Where the picker posts. Carries the token, which it cannot send as a header. */
  uploadAction: string
}) {
  const [files, setFiles] = useState<UploadFile[]>([])

  return (
    <div className="composer">
      <div className="column">
        <div className="composer__row">
          <ModelPicker prefs={prefs} disabled={disabled} onPick={onPick} />
        </div>
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
