# dsh-plugin-doc2video

把 PDF / PPT 变成带讲稿、配音和镜头的讲解视频——在 [DeepSeek Harness](https://github.com/deepseek-ai/deepseek-harness) 里，由 dsh 的模型写讲稿。

隔壁 [`../cordis.patch.yml`](../README.md) 那条 MCP 桥接不用写代码就能接上。这个插件存在，是为了那条路做不到的两件事：

**文件还是文件。** MCP 的调用方可能在另一台机器上，所以文档得 base64 塞进一次工具调用——一份 6MB 的 PPT 编码完 8MB，根本进不去。dsh 就在本机，所以 `doc2video_prepare` 收的是**路径**，字节从不进入对话。

**没人需要先起服务。** `baseUrl` 留空，插件自己把后端拉起来：端口是它挑的，token 是它当场生成的（不写盘、不上命令行），dsh 退出时一并收掉。用户只管跑 `dsh`。

## 装

```sh
dsh plugin --profile <profile> add dsh-plugin-doc2video
```

然后带上配置启动（或把 `cordis.patch.yml` 里那段 `insert` 追加进 `~/.dsh/profiles/<profile>/cordis.patch.yml`）：

```sh
dsh --profile <profile> --patch /绝对路径/integrations/dsh/plugin/cordis.patch.yml
```

后端本身要能起来：`pip install doc2video-agent` 之后 `command: doc2video` 即可；从源码跑就改成 `command: uv`、`args: ['run', '--project', '/绝对路径/doc2video-agent']`。

## 配置

| 字段 | 默认 | 说明 |
|---|---|---|
| `baseUrl` | `''` | 已经在跑的后端。留空则自己拉起一个 |
| `token` | `''` | 连已有后端时的 Bearer token；自己拉起时会当场生成 |
| `command` | `doc2video` | 怎么启动后端 |
| `args` | `[]` | `serve` 之前的参数，例如 `['run', '--project', '/path']` |
| `cwd` | `''` | 工作目录 |
| `storageDir` | `''` | 工程放哪。给了它，成片才能按本机路径回答 |
| `env` | `{}` | 交给后端的环境变量：配音引擎、语速、模型 key |
| `startupTimeoutMs` | `60000` | 等 `/health` 的上限 |
| `requestTimeoutMs` | `180000` | 单个请求的上限（解析 30 页 PDF 是最慢的那个） |

## 五个工具

| 工具 | 做什么 |
|---|---|
| `doc2video_prepare` | 按**本机路径**解析文档，返回逐页内容和每页的秒数/字数预算 |
| `doc2video_render` | 交回逐页讲稿，开始配音、镜头、渲染、质检。立刻返回 `job_id` |
| `doc2video_status` | 查任务进度 |
| `doc2video_revise` | 只改某几个场景，只重做这几个 |
| `doc2video_result` | 成片在哪、多长、每个场景的 id 和讲稿、质量分 |

一轮是这样走的：`prepare` → 模型逐页写讲稿 → `render` → 隔一会儿 `status` → `result`；要改哪几页再 `revise`。

三件事写进了工具描述里，因为不说就一定会错：

- **讲稿是调用方写的**，这个服务不持有模型。`prepare` 一次把页面文字、元素和预算全给出来，就是为了不用再问一轮。
- **预算是上限不是建议**。时长按字数估，超了成片就超时长，而**音频一旦合成，长度就改不动了**。
- **渲染是分钟级的**，所以起任务的工具立刻返回 `job_id`，不阻塞对话。

## 还没做的

- **进度只能轮询**。后端有 `GET /jobs/{id}/events`（SSE），插件还没用上。
- **没接 `ctx.jobs`**。接上之后就能用 dsh 自带的 `job_list` / `job_kill` 管渲染任务，现在得靠 `doc2video_status`。
- **非 macOS 默认没有声音**。后端默认 `macos_say`，别的平台会静默落到静音引擎——时间轴、字幕、镜头全对，就是没声音。跨平台先用 `env` 指定 `D2V_TTS_PROVIDER=edge`。

## 开发

```sh
npm install && npm run build      # 出 lib/
npm run typecheck
```

本地联调：`cd ~/.dsh/profiles/<profile> && pnpm add file:/绝对路径/integrations/dsh/plugin`。
