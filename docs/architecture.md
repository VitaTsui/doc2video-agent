# 架构说明

本文说明代码结构与立项技术方案的对应关系，以及实现过程中做出的关键取舍。
方案原件不随仓库分发，下表的「§N」即方案章节号。

## 分层

```
                     ┌──────────────────────────┐
                     │   Doc2Video Agent        │
                     │   planner + executor     │   agent/
                     └───────────┬──────────────┘
                                 │
        ┌────────────────────────┼────────────────────────┐
        ▼                        ▼                        ▼
  Document Skill          Narration Skill           Director Skill      skills/
  （理解文档）             （写讲稿+控时长）          （语义→视觉注意力）
        └────────────────────────┼────────────────────────┘
                                 ▼
                          Video Project                                 schemas/
                     （唯一业务真相来源）
                                 │
              ┌──────────────────┼──────────────────┐
              ▼                  ▼                  ▼
          TTS Tool        Motion / Timeline    GenVideo Tool           tools/
              │                  │                  │
              │        Remotion / FFmpeg Adapter    │
              └──────────────────┼──────────────────┘
                                 ▼
                            FFmpeg 合成
                                 ▼
                                MP4
```

| 方案章节 | 实现位置 |
| --- | --- |
| §5 Agent 职责边界 | `agent/planner.py`（意图→计划）、`agent/executor.py`（分阶段执行） |
| §6 Skill / Tool 分层 | `skills/`（业务）与 `tools/`（技术），Skill 只依赖 `tools` 的抽象接口 |
| §7 VideoProject | `schemas/project.py` |
| §8 三层中间模型 | `schemas/document.py`、`schemas/scene.py`、`schemas/timeline.py` |
| §9 Scene 数据结构 | `schemas/scene.py`，`Scene.content_hash()` 支撑增量渲染 |
| §10 Director Skill | `skills/director.py` |
| §11 Renderer Adapter | `tools/renderer/`（base / remotion / ffmpeg_adapter / genvideo） |
| §12 内容路由策略 | `skills/motion.py` 生成 ScenePlan，`tools/renderer/__init__.py` 选择 adapter |
| §13 Agent API | `api/routes/agent.py` |
| §15 端到端流程 | `agent/planner.py::FULL_PIPELINE` |
| §17 PPT 解析与渲染 | `tools/parsers/ppt_parser.py` + `tools/slides/`（三档保真度） |
| §19 验收指标 | `tests/` + `skills/review.py` |
| §20 风险对策 | 见下节 |

## 关键设计决策

### 1. 时间戳只有一个来源

导演动作的时间**从不由模型给出**。模型只回答「这句话在讲哪个元素、该用什么动作」，
`at` 与 `duration` 全部由 `skills/director.py` 从 TTS 的句级时间戳推导。

带来的性质：

- 同一个工程重复渲染，结果逐帧一致。
- 改配音（换音色、改语速）后，镜头会自动重新对齐，不需要重新问模型。
- 模型不需要理解「秒」这个概念，减少一类系统性错误。

### 2. 增量渲染建立在内容指纹上

`Scene.content_hash()` 覆盖讲稿、时长、画面资源、动作列表、音频路径与句级时间戳；
`RenderState.rendered_scenes` 记录每个场景上次渲染时的指纹。
`VideoProject.dirty_scenes()` 就是「需要重渲的场景」。

例外：换了渲染器时全量重渲——不同渲染器产出的编码参数不同，混在一起无法 concat。

### 3. 每个 Skill 都有确定性降级路径

`Skill.try_llm(fn, fallback)` 是统一入口：LLM 不可用或调用失败时，落到启发式规则。
这不是占位实现，而是让整条链路在无网络、无 Key 的情况下依然可跑、可测、可调试
（`tests/` 全部跑在这条路径上）。

代价是讲稿质量明显下降——`skills/review.py` 会以 `read_aloud`、`duration` 两条告警
如实反映出来，而不是假装成功。

### 4. 页面坐标 → 画面坐标的映射只有一处

`skills/layout.py::to_frame_area` 处理缩放与 letterbox 补边。
渲染器只接受 0..1 的归一化区域，因此不需要知道源文档的页面尺寸；
一旦这里算错，所有 adapter 会一致地错——便于定位，而不是各错各的。

### 5. 生成式视频的边界写进了类型里

`GenerativeVideoAdapter.available()` 恒为 `False`，`render_scene` 直接抛异常并说明原因。
这不是「还没做」，而是把方案 §12、§20 的约束固化在代码里：
**承载信息的画面必须来自确定性渲染**，生成式视频只做 B-roll。

### 6. 系统级依赖按「能否装进 Python 环境」分成两类

`tools/media_binaries.py` 统一解析 ffmpeg / ffprobe：**指定路径 → 系统 PATH → 环境内置 wheel**，
并把来源如实报给 `doctor` 与 `/health/capabilities`。这样一台机器上「能不能出片」是可查的，
而不是等到渲染阶段才炸。

- **ffmpeg 可以内置**：它是单个静态二进制，有现成的 Python wheel（`imageio-ffmpeg`）。
  代价是该 wheel 为 GPL 构建，且不含 ffprobe——后者用三级回退绕开（WAV 头 → ffprobe → 解析
  ffmpeg 输出），所以缺 ffprobe 对结果没有影响。
- **LibreOffice 不能内置**：它是完整的原生办公套件，不存在能装进 venv 的形态。
  因此它被降级为「保真度依赖」而不是「功能依赖」——缺了它 PPT 依然能解析、能出片，
  只是幻灯片走内置栅格化器。真要内置，只能进镜像（见 `Dockerfile`）。

这条边界决定了一个产品判断：**PDF 链路零系统依赖，PPT 链路的系统依赖只影响画面好看程度**。

**内置不等于同构**：同一个 wheel 在 macOS 上给的 ffmpeg 带 `drawtext`，在 Linux 上给的不带，
于是烧字幕会以 `Filter not found` 让整个渲染失败。所以 `media_binaries.has_filter()` 会在
构图前探测滤镜是否存在，缺失时只丢掉字幕并告警——**缺一个可选能力不该让整条渲染挂掉**。
`zoompan` / `drawbox` / `fade` 属于承载性滤镜，缺了它们没有可降级的余地，由测试直接断言。

### 7. 幻灯片渲染有三档，样式模型与语义模型分开

`tools/slides/` 把 PPTX 的**样式**抽成独立的渲染模型（主题色、字体、填充、渐变、
圆角、旋转、分组变换、表格），交给 Remotion 的 `Slides` 组合渲染——帧号 = 页号，
整份 deck 只 bundle 一次浏览器。

它与 `schemas/document.py` 的语义模型**刻意分开**：那边回答「这一页在讲什么」、
是导演推理的依据；这边回答「这一页长什么样」、只被渲染器消费。
因此这里增加渐变或旋转支持，永远不可能改变讲稿与镜头行为。

三档按保真度自动降级：LibreOffice（PowerPoint 自己的排版引擎）→ Chromium（复用
Remotion 已装的浏览器，无额外依赖）→ Pillow（只有几何）。

**图表是这条路径上收益最高的一块**，因为它通常正是导演要放大的对象。
`tools/slides/` 用 SVG 重绘图表（柱/条/堆叠/折线/面积/饼/环/散点），并且把同一份数据
经 `describe.py` 变成一句事实描述喂给讲稿——没有它，讲稿只知道「这里有个图表」，
必然退回到照读页面文字，也就是方案 §20 要防的第一条风险。

重绘图表时同样区分「还原」与「设计」：配色、图例开关、数据标签一律听原稿的；
只有原稿没规定的部分（网格线弱化、文字用墨色而非系列色、图例居中成组）才按通用实践处理。
饼图是个例外中的例外——它的身份由**类别**而非系列承载，所以按 PowerPoint 的做法
逐切片取主题 accent，早期用「基色+色相偏移」生成的版本两片蓝色几乎无法分辨。

**不猜测是这里的原则。** 早期版本按「level>0 就画项目符号」推断 bullet，结果给
PowerPoint 本不会显示 bullet 的纯文本框凭空加上了符号——与 LibreOffice 对比才发现。
现在只信任显式的 `buChar`/`buAutoNum`，仅对正文占位符做推断。
反过来，表格**必须**给默认样式：PowerPoint 永远会套用一个表格样式，渲染成裸网格
比用主题 accent 近似默认样式离原稿更远。

## 风险与对策落地

| 方案中的风险 | 代码中的对策 |
| --- | --- |
| AI 只是读 PPT | `prompts/narration.md` 明确禁止照读；`review.py` 用字符二元组重合度量化并告警 |
| 讲到的内容与画面不对应 | 讲稿阶段强制产出 `element_refs`，非法 id 在落库前被丢弃；导演动作只能指向真实元素 |
| 底层视频框架变化快 | `RendererAdapter` 抽象 + ScenePlan DSL，业务层不出现任何框架类型 |
| 生成式视频导致信息错误 | 见上「决策 5」 |
| 长任务失败 | 每个 stage 结束即落盘；`JobManager` 支持重试，且重试复用已生成的工程 |
| 修改成本过高 | Scene 化 + 内容指纹 + 音频指纹（`SceneAudio.text_hash`），改一页只重做一页 |

## 尚未实现（有意留白）

- **数字人、声音克隆**：MVP 明确不做。
- **PPT 原生动画还原**：只取动画结束态。
- **版式/母版文本继承**：`<a:lstStyle>` 链未解析，继承字号退化为按占位符类型取默认值。
- **`tableStyles.xml`**：表格样式用主题 accent 近似，未读取实际样式定义。
- **图表的次坐标轴与三维效果**：次坐标轴按单轴渲染（双轴本身也是反模式），3D 按平面渲染。
- **生成式视频接入**：adapter 已就位，未接服务商。
- **VLM 图表深读**：`llm.complete_json` 已支持传图，文档 Skill 目前只用文本；
  接图表页只需在 `DocumentSkill._render_prompt` 处附上 `images=[页面图]`。
- **分布式任务队列**：`agent/jobs.py` 为进程内实现，换 Celery / Temporal 只需替换该模块。
