/**
 * Bakes the handful of icons the window uses into a source file.
 *
 * Iconify fetches icon data from its public API at runtime. That is fine on a
 * web page and wrong here twice over: the desktop build's CSP allows no host
 * but the local backend, so the request is blocked and every icon silently
 * renders as nothing; and a desktop tool should not need the internet to draw
 * a paperclip.
 *
 * Whole sets are far too big (thousands of icons each) for the four we use, so
 * this pulls out exactly those four. Run `pnpm icons` after adding one.
 */
import { readFileSync, writeFileSync } from 'node:fs'

const WANTED = {
  'heroicons-outline': ['paper-clip', 'x'],
  tabler: ['arrow-up'],
  'eos-icons': ['loading'],
  // SecondConf draws this one itself; without it the confirm dialog opens
  // with a blank square where the question mark goes.
  mingcute: ['question-line'],
}

const icons = {}
for (const [set, names] of Object.entries(WANTED)) {
  const path = `node_modules/@iconify-json/${set}/icons.json`
  const data = JSON.parse(readFileSync(path, 'utf8'))
  for (const name of names) {
    const icon = data.icons[name]
    if (!icon) throw new Error(`${set}:${name} 不在这个图标集里`)
    icons[`${set}:${name}`] = {
      body: icon.body,
      width: icon.width ?? data.width ?? 24,
      height: icon.height ?? data.height ?? 24,
    }
  }
}

const file = `// 由 scripts/gen-icons.mjs 生成，请勿手改。改图标请改那个脚本再跑 \`pnpm icons\`。
//
// Iconify 默认在运行时联网取图标数据，而桌面版的 CSP 只放行本地后端——
// 请求被挡掉，图标就静默地什么都不显示。这里把用到的几个烤进源码。
import { addIcon } from '@iconify/react'

const ICONS: Record<string, { body: string; width: number; height: number }> = ${JSON.stringify(icons, null, 2)}

export function registerIcons() {
  for (const [name, icon] of Object.entries(ICONS)) {
    addIcon(name, icon)
  }
}
`
writeFileSync('src/icons.generated.ts', file)
console.log(`写入 ${Object.keys(icons).length} 个图标`)
