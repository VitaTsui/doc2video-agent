// 由 scripts/gen-icons.mjs 生成，请勿手改。改图标请改那个脚本再跑 `pnpm icons`。
//
// Iconify 默认在运行时联网取图标数据，而桌面版的 CSP 只放行本地后端——
// 请求被挡掉，图标就静默地什么都不显示。这里把用到的几个烤进源码。
import { addIcon } from '@iconify/react'

const ICONS: Record<string, { body: string; width: number; height: number }> = {
  "heroicons-outline:paper-clip": {
    "body": "<path fill=\"none\" stroke=\"currentColor\" stroke-linecap=\"round\" stroke-linejoin=\"round\" stroke-width=\"2\" d=\"m15.172 7l-6.586 6.586a2 2 0 1 0 2.828 2.828l6.414-6.586a4 4 0 0 0-5.656-5.656l-6.415 6.585a6 6 0 1 0 8.486 8.486L20.5 13\"/>",
    "width": 24,
    "height": 24
  },
  "heroicons-outline:x": {
    "body": "<path fill=\"none\" stroke=\"currentColor\" stroke-linecap=\"round\" stroke-linejoin=\"round\" stroke-width=\"2\" d=\"M6 18L18 6M6 6l12 12\"/>",
    "width": 24,
    "height": 24
  },
  "tabler:arrow-up": {
    "body": "<path fill=\"none\" stroke=\"currentColor\" stroke-linecap=\"round\" stroke-linejoin=\"round\" stroke-width=\"2\" d=\"M12 5v14m6-8l-6-6m-6 6l6-6\"/>",
    "width": 24,
    "height": 24
  },
  "eos-icons:loading": {
    "body": "<path fill=\"currentColor\" d=\"M12 2A10 10 0 1 0 22 12A10 10 0 0 0 12 2Zm0 18a8 8 0 1 1 8-8A8 8 0 0 1 12 20Z\" opacity=\".5\"/><path fill=\"currentColor\" d=\"M20 12h2A10 10 0 0 0 12 2V4A8 8 0 0 1 20 12Z\"><animateTransform attributeName=\"transform\" dur=\"1s\" from=\"0 12 12\" repeatCount=\"indefinite\" to=\"360 12 12\" type=\"rotate\"/></path>",
    "width": 24,
    "height": 24
  },
  "mingcute:question-line": {
    "body": "<path fill=\"none\" stroke=\"currentColor\" stroke-linecap=\"round\" stroke-linejoin=\"round\" stroke-width=\"2\" d=\"M12 17h.002m-2.627-6.875a2.625 2.625 0 1 1 3.601 2.438c-.512.205-.976.635-.976 1.187V14m9-2a9 9 0 1 1-18 0a9 9 0 0 1 18 0\"/>",
    "width": 24,
    "height": 24
  }
}

export function registerIcons() {
  for (const [name, icon] of Object.entries(ICONS)) {
    addIcon(name, icon)
  }
}
