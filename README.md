# Doc2Video Agent

[![license](https://img.shields.io/badge/license-MIT-blue.svg)](./LICENSE)
[![python](https://img.shields.io/badge/python-3.11%2B-3776AB.svg)](./pyproject.toml)
[![renderer](https://img.shields.io/badge/renderer-Remotion%20%7C%20FFmpeg-orange.svg)](./renderer)

把 **PDF / PPT 变成讲解视频**的确定性引擎，以 MCP 工具的形式给模型调用。

> **它本身不持有模型，也不需要任何模型 Key。**
> 讲稿由调用方（Claude / ChatGPT / 你自己的 Agent）写好传进来；
> 它负责解析幻灯片、时长预算、配音、镜头、时间轴、渲染合成与质检——
> 这些是该由代码而不是模型来做的部分。

> 核心资产不是 MP4，而是**可持续修改、可增量渲染的 VideoProject**。
> 「第 7 页太长了，压缩到 20 秒」只会重写一页讲稿、重配一段音、重渲一个片段。

```
你 → 模型：把这份 company_intro.pptx 做成 8 分钟讲解视频，面向企业客户

模型 → prepare_project()   拿到逐页内容 + 每页时长/字数预算
模型                        读完页面，按预算写逐页讲稿   ← 唯一需要模型的一步
模型 → render_video()      配音 → 镜头 → 时间轴 → 渲染 → 质检
模型 → project_summary()   读质量分与质检结果，必要时 revise_scenes 局部重来
```

## 设计要点

| 原则 | 落地方式 |
| --- | --- |
| **语义归调用方，确定性归服务端** | 服务端不含任何模型调用；讲稿从 `render_video(narrations)` 传入 |
| 时长在写之前就定好 | `prepare_project` 返回每页的秒数与字数预算——音频一旦生成，长度就改不动了 |
| 信息型画面坚持确定性渲染 | 生成式视频仅作为 B-roll，`GenerativeVideoAdapter` 显式不承载数据画面 |
| 改一页只重做一页 | `revise_scenes` 只重配音、重渲受影响的场景，其余片段复用 |
| 长任务可观测、可重试 | 后台任务 + 分阶段落盘，失败可从当前工程续跑 |
| 质检是结构化的 | 缺音画、镜头指向不存在的元素、节奏、字幕溢出、讲稿照读页面——都是可计算的 |

## 技术栈

| 分类 | 选型 |
| --- | --- |
| Agent 后端 | Python 3.11+、FastAPI、Pydantic v2 |
| PDF 解析 | PyMuPDF（文本块 / 图片 / bbox / 高清页面渲染） |
| PPT 解析 | python-pptx + OOXML；幻灯片渲染三档：LibreOffice / Chromium / 内置栅格化器 |
| TTS | 可插拔 provider（内置 macOS `say`、静音兜底） |
| 渲染 | **Remotion**（首选，React 确定性渲染）/ **FFmpeg**（纯 filter 兜底） |
| 编码合成 | FFmpeg（拼接、混音、封装） |
| 存储 | 文件系统（一个工程一个目录，可直接拷走重渲） |

## 快速开始

```bash
# 1. 装依赖（bundled 会把 ffmpeg 与 ffprobe 一起装进虚拟环境）
uv venv --python 3.12
uv pip install -e ".[bundled,dev]"

# 2. 体检
uv run doc2video doctor

# 3. 起服务（MCP 挂在 /mcp）
D2V_API_TOKEN=$(openssl rand -hex 32) uv run doc2video serve
```

不需要任何模型 Key。想要更好的镜头表现力再装 Remotion：

```bash
cd renderer && pnpm install
```

## 依赖内置

系统级依赖里，**ffmpeg 已经内置，LibreOffice 不能内置**——两者性质不同。

### ffmpeg / ffprobe：都已内置

`pip install '.[bundled]'` 会把静态 ffmpeg **和 ffprobe** 一起装进虚拟环境。查找顺序：

```
D2V_FFMPEG_PATH 指定 → 系统 PATH → 环境内置的 wheel
```

`doc2video doctor` 会显示实际用的是哪一份，以及这份构建有没有字幕滤镜：

```
✓ ffmpeg   [内置] 编码、拼接、混音、封装；也是纯 ffmpeg 渲染器的依赖
✓ ffprobe  [内置] 音频时长探测（缺失时改用 ffmpeg 解析，不影响结果）

滤镜：
✓ drawtext 烧录字幕（缺失时只跳过字幕，渲染照常完成）
```

内置的 wheel 按平台选，任何一个平台上只会装其中一个：

| 平台 | wheel | 带什么 |
| --- | --- | --- |
| macOS / Linux x86_64 / Windows x64 | `ffmpeg-binaries`（ffmpeg 6.0） | ffmpeg + **ffprobe**，且各平台构建都有 `drawtext` |
| Linux arm64 | `imageio-ffmpeg`（ffmpeg 7.1） | 只有 ffmpeg，且该构建**没有 `drawtext`** |

两个已知代价，都已在代码里处理：

- **不同平台的构建功能不同**。Linux arm64 那一档烧不了字幕。渲染前会用
  `media_binaries.has_filter()` 探测，缺失时只跳过字幕并告警，不会让整个渲染失败；
  容器镜像里额外装了系统 ffmpeg（PATH 优先），字幕正常。时长探测同样是
  WAV 头 → ffprobe → 解析 ffmpeg 输出三级回退，缺谁都得出一样的结果。
- **两份二进制都是 GPL 构建**（`--enable-gpl --enable-libx264`）。自用、内部部署没问题；
  要随闭源商业产品分发时，需换成 LGPL 构建（`--disable-gpl`，用 openh264 或系统编码器）
  或改为让用户自行安装。这是法务问题，不是技术问题。

> `ffmpeg-binaries` 还会往环境的 `bin/` 里装一个同名的 Python 启动脚本。它会遮住
> PATH 上的真二进制，所以 PATH 查找会跳过解释器所在目录，直接用 wheel 里的原始二进制。

### LibreOffice：内置不了，但大部分场景已经不需要它

LibreOffice 是 400MB~1GB 的原生套件，没有任何 pip / npm 包能把它装进 Python 环境，
「内置」只能是把它打进镜像。**它只影响 PPT/PPTX 的幻灯片渲染保真度**——PDF 由 PyMuPDF
自己渲染，完全不需要它。

幻灯片渲染有三档，自动按保真度降级：

| 档位 | 依赖 | 保真度 |
| --- | --- | --- |
| **LibreOffice** | 系统安装 / Docker 镜像 | 最高：PowerPoint 自己的排版引擎 |
| **Chromium** | 复用 Remotion 已装的浏览器，无额外依赖 | 高：主题色、字体、填充/渐变、圆角、旋转、层级、表格、**图表** |
| **内置栅格化器** | 无 | 低：只按 shape 几何绘制 |

Chromium 档把 PPTX 的样式抽成渲染模型（`tools/slides/`），交给一个
`Slides` Remotion 组合渲染——**帧号 = 页号**，整份 deck 只 bundle 一次。
装了 `renderer/` 依赖就自动启用，不需要 LibreOffice。

```bash
docker build -t doc2video-agent .    # 需要 LibreOffice 档时
docker run -p 8400:8400 -v "$PWD/data:/data" doc2video-agent
```

**图表**由 SVG 重绘，支持柱状 / 条形 / 堆叠 / 折线 / 面积 / 饼 / 环形 / 散点，
配色取自 deck 自身的系列填充或主题 accent（PowerPoint 的取色顺序），
数据标签只在原稿开启时才画。图表数据同时进入语义层——讲稿能拿到
「Q1 至 Q4；生成视频数 从 1,200 增长到 5,200（+333%）」这样的事实，而不是只知道「这里有个图表」。

**文字外观取自继承链**而不是靠占位符类型猜：`<a:lstStyle>` 沿
「presentation 默认 → 母版 `txStyles` → 母版占位符 → 版式占位符 → 形状 → 段落/文字块」
逐级合并，字号、颜色、字体、对齐、项目符号都按 PowerPoint 的规则解析，
自动缩放（`normAutofit`）的收缩比例也会带到渲染。表格样式读 `ppt/tableStyles.xml`
的真实定义（首行、镶边行、整表填充与边框色）。

**艺术字**（描边、文字渐变、阴影/发光）与**图案填充**（54 种预设网底）都会还原；
图表支持**组合图**（柱+线混排，各按自己的类型画）、**次坐标轴**（右侧独立刻度）
与 **3D 图表**——3D 数据仍按平面比例绘制，只把立体面画出来，读数不会被透视扭曲。

Chromium 档未还原的部分（会退化成合理默认值，不会报错）：
艺术字的弯曲变形（`prstTxWarp`）、图片填充、雷达/股价/曲面图。

> 旧版二进制 `.ppt` 是唯一**硬依赖** LibreOffice 的场景：python-pptx 读不了它，
> 需要先转成 `.pptx`。新版 `.pptx` 在任何环境下都能解析，区别只在渲染保真度。

## API

对外接口保持 Agent 化，核心入口只有一个。

### `POST /agent/run`

首次生成（multipart）：

```bash
curl -X POST http://127.0.0.1:8400/agent/run \
  -F "files=@demo.pptx" \
  -F "message=生成一个8分钟科技风的产品讲解视频"
```

继续修改（JSON）：

```bash
curl -X POST http://127.0.0.1:8400/agent/run \
  -H "Content-Type: application/json" \
  -d '{"project_id": "proj_xxxx", "message": "第7页太长了，压缩到20秒"}'
```

默认异步返回 `job_id`；加 `wait=true` 则同步返回结果。

### 其余接口

| 方法 | 路径 | 说明 |
| --- | --- | --- |
| GET | `/health/capabilities` | LLM / TTS / 渲染器 / 外部二进制的可用性 |
| GET | `/jobs`、`/jobs/{id}` | 任务状态与进度 |
| POST | `/jobs/{id}/retry` | 失败任务重试（复用已生成的工程，不重头再来） |
| GET | `/projects` | 工程列表 |
| GET | `/projects/{id}` | 完整 VideoProject |
| GET | `/projects/{id}/scenes` | 场景列表（讲稿、时长、镜头动作） |
| GET | `/projects/{id}/timeline` | 绝对时间轴 |
| GET | `/projects/{id}/review` | 质检结果 |
| GET | `/projects/{id}/quality` | 质量分与各维度得分 |
| GET | `/projects/{id}/telemetry` | 上次运行的分阶段耗时、模型调用与成本 |
| GET | `/metrics`、`/metrics/runs` | 跨运行统计与灰度分支对照 |
| GET | `/projects/{id}/video` | 下载成片 |
| GET | `/projects/{id}/assets/{path}` | 预览页面渲染图、配音等资源 |

## 核心数据模型

三层中间模型，各回答一个问题：

- **Document Model** — 这份文档在讲什么：章节、页面类型、元素与 bbox。
- **Scene Model** — 这一段视频怎么讲：讲稿、句级片段、时长、镜头动作。**Agent 的基本编辑单位。**
- **Timeline Model** — 第几秒发生什么：画面、音频、字幕、动作，全部绝对时间。

```jsonc
{
  "project_id": "proj_001",
  "source": { "type": "pptx", "file": "demo.pptx" },
  "intent": { "audience": "企业客户", "style": "professional", "duration": 480 },
  "document": { "pages": [], "sections": [] },
  "scenes": [
    {
      "scene_id": "scene_06",
      "source_page": 6,
      "duration": 24.8,
      "narration": "接下来我们来看整个系统架构……",
      "segments": [{ "id": "scene_06_s02", "start": 7.2, "end": 11.0, "element_refs": ["p06_e03_rag"] }],
      "actions": [{ "at": 7.5, "type": "zoom", "target": "p06_e03_rag", "duration": 3.5 }]
    }
  ],
  "timeline": {},
  "render": { "renderer": "remotion", "rendered_scenes": {} }
}
```

`render.rendered_scenes` 记录每个场景上次渲染时的内容指纹，改哪个场景就只重渲哪个。

## 目录结构

```
doc2video-agent/
├── doc2video/
│   ├── agent/          # planner（意图→计划）、executor（分阶段执行）、jobs（任务）
│   ├── skills/         # document / narration / voice / director / layout / motion / review
│   ├── tools/          # parsers、slides、llm、tts、ffmpeg、renderer（adapter）
│   ├── schemas/        # Document / Scene / Timeline / VideoProject
│   ├── prompts/        # 各 Skill 的提示词
│   ├── storage/        # 工程持久化
│   ├── api/            # FastAPI 路由
│   └── cli.py
├── renderer/           # Remotion 工程（Scene 镜头组合 + Slides 幻灯片栅格化）
├── docs/               # 架构说明与设计取舍
└── tests/
```

## 端到端流程

1. 用户上传 PDF/PPT 并给出自然语言目标 →
2. Agent 解析意图，生成 Video Intent →
3. Document Skill 解析页面并生成 Document Model →
4. Narration Skill 按全文上下文与时长预算生成逐页讲稿 →
5. TTS 生成音频与句级时间戳 →
6. Director Skill 把讲稿片段绑定到页面元素，生成镜头动作 →
7. Motion 展开为绝对时间轴与渲染计划 →
8. 渲染器逐场景出片，FFmpeg 拼接、混音、封装 →
9. Review Skill 对时长、音画、字幕、事实一致性做质检。

## 依赖与降级说明

项目对外部依赖全部做了**显式降级**，缺什么就少什么能力，不会整条链路失败：

| 缺失 | 影响 | 降级行为 |
| --- | --- | --- |
| 调用方没给某页讲稿 | 那一页内容无意义 | 用占位文本，并在运行记录里计一次降级、在返回值里列出缺哪几页 |
| LibreOffice (`soffice`) | PPT 幻灯片保真度 | 降级到 Chromium 渲染（保留主题色/字体/填充）；两者都缺才用内置栅格化器 |
| Node / Remotion | 镜头表现力 | 自动回退到 FFmpeg 渲染器 |
| `say`（非 macOS） | 真实语音 | 生成等时长静音轨，时间轴与字幕仍然正确 |
| `ffprobe`（Linux arm64） | 无 | 改用 WAV 头 / ffmpeg 输出解析时长 |
| `drawtext` 滤镜（Linux arm64） | 无字幕 | 只跳过字幕并告警，其余画面照常渲染 |
| `ffmpeg` 且未装 `[bundled]` | 无法产出成片 | 前面所有阶段照常完成并落盘，渲染阶段明确报缺失 |

用 `doc2video doctor` 可以一次看清当前机器处在哪一档。

## 运行可观测性

每次 `agent.run` 都会留下一条**运行记录**：分阶段耗时、每次模型调用的 token 与花费、
哪些步骤降级了、这次走的是哪条灰度分支，以及最终的质量分。记录同时落在**工程里**
（`project.telemetry`，回答「这支视频怎么样」）和 `storage/runs.jsonl` **总账**里
（回答「最近是不是变慢了、一支视频要花多少钱」）。

```bash
uv run doc2video metrics     # 跨运行的耗时 / 成本 / 质量 / 灰度对照
uv run doc2video show <id>   # 单个工程的质量分与上次运行开销
```

| 关注点 | 落地方式 |
| --- | --- |
| **监控** | 每个阶段计时并记录成败；**降级也计数**——链路降级后仍然「成功」，不数就跟正常跑完毫无区别 |
| **成本统计** | 归因到发起调用的 stage 与 skill。API 路径按价目表折算，CLI 路径直接用它自己报的 `total_cost_usd`；模型没有公开价就报「未知」，不报 0 |
| **质量评估** | 从 review 已有的检查里折算出 0–100 分，分完整度/节奏/原创度/镜头/字幕五个维度。**按比例算而不是数条数**，所以分数不随页数漂移 |
| **灰度** | 特性开关带放量比例，按 `flag:project_id` 哈希决定分支 |

灰度的哈希**必须**按工程稳定：同一个工程下周再编辑时若换了渲染器，新旧片段编码不同、
根本拼不起来。放量比例调大只会新增工程，不会把已在新路径上的工程弹出去。

```bash
D2V_FLAGS='{"renderer_remotion": 25}'   # 只有 25% 的工程用 Remotion 渲染
```

开关接在真实分叉上（渲染器选择），每次运行记下自己走的分支，
`doc2video metrics` 于是能给出**两条分支的成本与质量对照**——这才让「要不要扩量」
变成一个看数据的决定，而不是拍脑袋。

## 研发里程碑

| 阶段 | 内容 | 状态 |
| --- | --- | --- |
| M0 | 端到端可行性：文档 → 讲稿 → TTS → 视频 | ✅ 本仓库 |
| M1 | Document Model、元素绑定、时长控制 | ✅ 本仓库 |
| M2 | Director + Timeline、Zoom/Highlight/Pointer | ✅ 本仓库 |
| M3 | 统一 `/agent/run`、对话修改、场景级增量渲染 | ✅ 本仓库 |
| M4 | Beta：监控、成本统计、质量评估、灰度 | ✅ 本仓库 |

MVP 暂不包含：数字人、声音克隆、复杂 PPT 原生动画恢复、重型在线剪辑器、生成式视频大规模使用。

## 开发

```bash
uv run pytest             # 测试
uv run ruff check .       # Lint
uv run doc2video export-schemas   # 导出 JSON Schema
cd renderer && pnpm typecheck     # 渲染器类型检查
```

## License

[MIT](./LICENSE)
