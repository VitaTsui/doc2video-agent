import React from 'react'
import ReactDOM from 'react-dom/client'
import { ConfigProvider } from 'antd'
import zhCN from 'antd/locale/zh_CN'
// The library's components are antd's underneath; without antd's reset they
// render unstyled — stray list bullets, no input border, default blue buttons.
import 'antd/dist/reset.css'
// The library's components are written against its own design tokens
// (--vita-border, --vita-control-height, …). Without this sheet every one of
// those custom properties is undefined, and the components render with no
// border, no background and no height — which reads as "no styles at all".
import '@hsu-react/ui/es/styles/tokens.scss'

import { App } from './App'
import { registerIcons } from './icons.generated'
import './theme.css'

registerIcons()

// Dev only, and only when the shell is absent — see devMock.ts.
if (import.meta.env.DEV) {
  const { installDevMock } = await import('./devMock')
  installDevMock()
}

/**
 * How much width this platform's scrollbar takes.
 *
 * The transcript scrolls and the composer does not, so without this they are
 * centred inside boxes of different widths and the conversation sits slightly
 * left of the input beneath it. Invisible on macOS, where scrollbars overlay;
 * plain on Windows, where they do not.
 */
function measureGutter() {
  const probe = document.createElement('div')
  probe.style.cssText = 'position:absolute;visibility:hidden;overflow:scroll;width:100px;height:100px'
  document.body.append(probe)
  const gutter = probe.offsetWidth - probe.clientWidth
  probe.remove()
  document.documentElement.style.setProperty('--gutter', `${gutter}px`)
}

measureGutter()

ReactDOM.createRoot(document.getElementById('root')!).render(
  <React.StrictMode>
    <ConfigProvider
      locale={zhCN}
      // antd pushes a space between two CJK characters in a button, so
      // 「关闭」 is drawn as 「关 闭」. That convention belongs to a different
      // typographic tradition than the rest of this window, and next to
      // 「添加模型」 — four characters, untouched — it reads as a mistake.
      button={{ autoInsertSpace: false }}
      theme={{
        token: {
          colorPrimary: '#c96442',
          colorBgContainer: '#ffffff',
          colorBorder: '#e8e5dd',
          borderRadius: 8,
          fontFamily:
            '-apple-system, BlinkMacSystemFont, "PingFang SC", "Hiragino Sans GB", sans-serif',
        },
      }}
    >
      <App />
    </ConfigProvider>
  </React.StrictMode>,
)
