/** The input line: a message, optionally with a deck attached. */

import { useRef, useState } from 'react'

export function Composer({
  disabled,
  hint,
  onSend,
  onDeck,
}: {
  disabled: boolean
  hint: string
  onSend: (text: string) => void | Promise<void>
  onDeck: (file: File, brief: string) => void | Promise<void>
}) {
  const [text, setText] = useState('')
  const [file, setFile] = useState<File | null>(null)
  const picker = useRef<HTMLInputElement>(null)
  const input = useRef<HTMLTextAreaElement>(null)

  function submit() {
    if (disabled) return
    if (file) {
      void onDeck(file, text.trim())
      setFile(null)
      setText('')
      return
    }
    if (!text.trim()) return
    void onSend(text.trim())
    setText('')
  }

  return (
    <div className="composer">
      <div className="column">
        {file && (
          <div className="composer__attachment">
            {file.name}
            <button type="button" onClick={() => setFile(null)} aria-label="移除">
              ✕
            </button>
          </div>
        )}
        <div className="composer__box">
          <input
            ref={picker}
            type="file"
            accept=".pdf,.ppt,.pptx"
            hidden
            // Held until send rather than uploaded on pick, so the file and the
            // sentence describing what to do with it arrive in the same turn.
            onChange={(e) => {
              const picked = e.target.files?.[0]
              if (picked) setFile(picked)
              e.target.value = ''
              input.current?.focus()
            }}
          />
          <button
            type="button"
            className="composer__icon"
            disabled={disabled}
            title="附加 PPT / PDF"
            onClick={() => picker.current?.click()}
          >
            <Clip />
          </button>
          <textarea
            ref={input}
            className="composer__input"
            rows={1}
            value={text}
            disabled={disabled}
            placeholder={hint}
            onChange={(e) => {
              setText(e.target.value)
              e.target.style.height = 'auto'
              e.target.style.height = `${Math.min(e.target.scrollHeight, 180)}px`
            }}
            onKeyDown={(e) => {
              // Enter sends; Shift+Enter is a newline, as everywhere else.
              if (e.key === 'Enter' && !e.shiftKey) {
                e.preventDefault()
                submit()
              }
            }}
          />
          <button
            type="button"
            className="composer__send"
            disabled={disabled || (!text.trim() && !file)}
            onClick={submit}
            aria-label="发送"
          >
            <Arrow />
          </button>
        </div>
        <div className="composer__hint">Enter 发送，Shift+Enter 换行</div>
      </div>
    </div>
  )
}

const Clip = () => (
  <svg width="17" height="17" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
    <path d="M21.4 11.05 12.25 20.2a5.5 5.5 0 0 1-7.78-7.78l9.2-9.19a3.67 3.67 0 1 1 5.18 5.18l-9.2 9.2a1.83 1.83 0 1 1-2.59-2.6l8.5-8.49" />
  </svg>
)

const Arrow = () => (
  <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.4">
    <path d="M12 19V5M5 12l7-7 7 7" />
  </svg>
)
