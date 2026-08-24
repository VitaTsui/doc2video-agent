# 在 DeepSeek Harness（dsh）里用 doc2video

把这个服务挂进 [dsh](https://github.com/deepseek-ai/deepseek-harness)，dsh 里的模型就能自己投文档、写讲稿、出片。

不需要为此写任何代码：dsh 自带 `@deepseek-ai/dsh-mcp-client`，能把任意 MCP server 的工具注册成原生工具；而本服务的 Agent 本来就以 MCP 的形式挂在 `/mcp`。

两边的分工也正好对得上——**本服务不持有模型，讲稿由调用方写**，在 dsh 里调用方就是 dsh 的模型。

## 三步

**一、起后端**

```sh
D2V_API_TOKEN=<自己定一个> doc2video serve --port 8393
```

**二、给 profile 装上桥接插件**（每个 profile 装一次）

```sh
dsh plugin --profile headless add @deepseek-ai/dsh-mcp-client
```

**三、带上这份配置启动**

```sh
D2V_API_TOKEN=<同一个> dsh --profile headless \
  --patch /绝对路径/doc2video-agent/integrations/dsh/cordis.patch.yml \
  "用 doc2video 列出已有的项目"
```

`--patch` 是叠加的，不动 profile 自己的配置；想常驻就把 `cordis.patch.yml` 里那段 `insert` 追加到 `~/.dsh/profiles/<profile>/cordis.patch.yml`。

## 模型会多出来的 8 个工具

| 工具 | 做什么 |
|---|---|
| `mcp__doc2video__upload_source` | 上传 PDF / PPT / PPTX（base64），拿 `upload_id` |
| `mcp__doc2video__prepare_project` | 解析文档，返回每页内容**和每页的字数预算** |
| `mcp__doc2video__render_video` | 把写好的逐页讲稿交回来，开始配音、镜头、渲染、质检 |
| `mcp__doc2video__revise_scenes` | 只改某几页的讲稿，只重渲这几页 |
| `mcp__doc2video__job_status` | 上面两个立刻返回 `job_id`，进度在这里查 |
| `mcp__doc2video__project_summary` | 一次生成之后值得知道的一切：时长、场景、质量分 |
| `mcp__doc2video__list_projects` | 这台机器上的所有项目 |
| `mcp__doc2video__video_download_path` | 成片在哪 |

一次完整的对话是这样走的：`upload_source` → `prepare_project`（模型看到每页内容和字数预算）→ 模型逐页写讲稿 → `render_video` → 轮询 `job_status` → `video_download_path`。

渲染是分钟级的，所以没有任何一个工具会阻塞：开工的工具立刻返回 `job_id`。

## 几个坑，先说清楚

**URL 末尾的斜杠不能省。** `/mcp` 会 307 跳到 `/mcp/`，而 307 之后的 POST 不是每个客户端都会原样重发。配置里写的是 `/mcp/`。

**`!!js` 表达式里有反引号就要整条引起来。** 反引号是 YAML 的保留字符，`Authorization: !!js \`Bearer ...\`` 会直接解析失败。

**投文档只能走 base64。** MCP 的调用方可能在另一台机器上，所以 `upload_source` 收的是 base64。几 MB 的 PPT 编码完更大，塞进一次工具调用不现实——这条通道适合小文件；大文件先用 `POST /uploads`（multipart）传，再把 `upload_id` 交给模型。

**非 macOS 默认没有声音。** 默认配音引擎是 `macos_say`，别的平台会静默落到静音引擎：时间轴、字幕、镜头全对，就是没声音。跨平台先配 `edge` 或 `kokoro`。

**进度只能轮询。** `job_status` 没有推送，渲染阶段可能几分钟不动。

## 撤掉

```sh
dsh plugin --profile headless remove @deepseek-ai/dsh-mcp-client
```

启动时不带 `--patch` 就等于没挂过。
