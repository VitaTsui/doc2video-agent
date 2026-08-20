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

`Skill.try_llm(fn, fallback)` 是统一入口：没配模型、调用失败、返回不合法——三种情况一视同仁地落到启发式规则，并在运行记录里留下降级原因。
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
比用主题 accent 近似默认样式离原稿更远——只有在 `ppt/tableStyles.xml` 里查不到
样式定义时才走这条近似路径。

### 8. 文字的外观来自继承链，不来自占位符类型

真实的 deck 几乎不在幻灯片上写任何格式：正文的字号、颜色、对齐、项目符号全在版式、
母版，或母版的 `titleStyle` / `bodyStyle` / `otherStyle` 里。只读幻灯片能拿到**文字**、
拿不到**外观**，这也是早期版本只能「按占位符类型猜一个字号」的原因。

`tools/slides/inherit.py` 按 OOXML 的顺序合并整条链（后者覆盖前者）：

1. presentation 的 `<p:defaultTextStyle>`
2. 母版 `<p:txStyles>`，按占位符类型选 title / body / other
3. 母版上对应的占位符
4. 版式上对应的占位符
5. 形状自己的 `<a:lstStyle>`
6. 段落 / 文字块上的直接属性

每一项都用 `None` 表示「这一级没说」，因此合并永远保留更具体的那个值；
剩下的 `None` 才轮到硬编码兜底。占位符的匹配跟 PowerPoint 一致：先按 `idx`，
再按类型，最后允许 body 家族（obj/tbl/chart/…）互相继承。
`normAutofit` 的 `fontScale` 作为一个乘数单独取出，落在所有解析出的字号上。

效果是可以量化的：同一份标准版式的 deck，原来正文三级全是 18pt、没有项目符号，
现在是 32 / 28 / 24pt 与 `•` / `–` / `•`，标题也从左对齐回到居中。

### 9. 页面视觉理解只在需要时附图

`DocumentSkill` 决定的是「这一页在讲什么」，而文字抽取恰恰在**意义只存在于画面上**
的页面最不可靠——架构图、图表、整屏截图，抽出来往往只剩几个游离标签。
这类页面把渲染图一并发给模型，让它看图判断页面类型、要点与元素重要性。

图很贵，所以不是每页都发：`_visual_weight()` 按图表 / 图片 / 表格元素与文字量给页面
打分（文字少于阈值本身就是一个信号），每批只带权重最高的几页，并在 prompt 里
按页码点名说明「随附了哪几页的图」——否则模型无法把图和页对上。
`llm.complete_json` 本来就支持传图，这里只是决定**传哪几张**。不是每个 provider 都能
看图：把本机 CLI Agent 当模型用时协议里只有一段任务文本，所以它 `supports_images()`
报 False，这一步就只发文字——而不是发一堆它读不到的字节。

### 10. 图表的真相在 XML 里，不在 `chart_type` 里

python-pptx 的 `PlotFactory` 只认九个绘图标签，`chart.chart_type` 也只报**一个**类型。
两个后果都很严重：

* **3D 图表整个丢失。** `bar3DChart` / `pie3DChart` 会直接抛 `unsupported plot type`，
  `chart.series` 返回空，图表被整块丢掉——而讲稿还在讲这张图。企业 PPT 里 3D 柱状图
  再普通不过，留下一块白比画错更糟。
* **组合图被画成柱状图。** 一个绘图区里可以同时有 `<c:barChart>` 和 `<c:lineChart>`，
  报成一个类型后，折线系列会被当柱子画。

所以 `chart_xml.py` 直接读绘图区，并且按文档顺序返回，正好和 `chart.series` 对齐：
系列数据（名称、值、显式颜色，缺失点保留为空洞而不是 0）、每条系列各自的绘图类型、
以及它挂在哪根值轴上。python-pptx 能打开的图仍走它的路径，读不了的才用 XML 兜底。

**次坐标轴同理，而且更隐蔽**：图上有两根值轴时，`chart.value_axis` 只会挑其中一根回答。
拿它的上下界去画柱子，结果是四根柱子全部顶到天花板——因为它给的是那根 0–0.5 的百分比轴。
只要绘图区解析成功，刻度就以 XML 为准，python-pptx 的值只作为解析失败时的兜底。

渲染侧对两个「反模式」的态度是一致的：**双轴和 3D 都照画，但几何保持诚实**。
双轴是经典的误导性图表，可原稿的读者本来就是对着两根轴读的，压成一根轴反而更失真；
3D 只把立体面画在柱子上方，正面仍从基线画到真实数值——读者量到的高度就是数字本身。

### 11. 读一个属性可能会改掉文件

艺术字（描边、文字渐变、阴影）全在 `<a:rPr>` 里，python-pptx 只暴露其中的纯色。
补读这些属性时踩到一个反直觉的坑：**`run.font.color` 是破坏性的**——
python-pptx 的 `ColorFormat` 会调用 `get_or_change_to_solidFill()`，
于是「问一下这个 run 是什么颜色」本身就把它的 `<a:gradFill>` 换成了空的 `<a:solidFill>`。

所以 `_paragraph` 里艺术字必须**先读**，顺序不是风格问题而是正确性问题，
`tests/test_fills_and_effects.py` 里有一条测试专门钉住它。

图案填充是同一类「不还原就等于删掉」的东西：54 种预设网底以前被当作不支持直接丢掉，
而丢掉填充的形状会变成透明——那比画得不够准更不像原稿。因此每个预设都必须落到某个结果上，
线型的用 `repeating-linear-gradient`，百分比网底直接按比例混色（幻灯片尺度上二者无法分辨，
而混色在缩放和视频压缩下活得更好），实在没映射的也退成两色混合。

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
- **艺术字的弯曲变形**：`a:prstTxWarp` 没有 CSS 对应物，沿路径弯曲的文字按直排渲染。
- **图片填充**：形状的 `blipFill` 未取图，退化为无填充。
- **雷达 / 股价 / 曲面图**：没有对应的渲染类型，目前按柱状图画——这是唯一还会
  「画成别的东西」的情况，比留白更需要补上。
- **生成式视频接入**：adapter 已就位，未接服务商。
- **分布式任务队列**：`agent/jobs.py` 为进程内实现，换 Celery / Temporal 只需替换该模块。

## 评估过、决定不引入的

留在这里是因为它们都不是坏主意，只是量完之后不划算。下次有人再提，先看这一节。

### Docling（2026-08-20 评估）

把 PDF / PPTX / DOCX 统一成一个结构化文档模型，看起来正好该做 Document Tool 的
底座。拿石化那份 30 页真实方案量了一遍（`scripts/bench_parser.py`，docling 2.120）：

| 指标 | 本项目 `pdf_parser` | docling |
| --- | --- | --- |
| 元素数 | 487 | 437 |
| 耗时 | 2.3s | 184.3s（**80 倍**） |
| 峰值内存 | 195MB | 4769MB（**24 倍**） |
| 依赖 | pymupdf | 103 个包，含 torch |
| 坐标系 | 已经是渲染页像素、左上原点 | PDF 点、左下原点，要重映射 |
| 表格结构 | 没有 | **也没有**（这份文档识别出 0 个 table） |

决定：**不引入**。三条理由，按分量排：

1. **它最值得引入的那件事没发生。** 表格结构是它的招牌，而在这份真实文档上它
   一个 table 都没识别出来——第 15 页那个 4×4 矩阵回来的仍然是一堆
   text / section_header，跟我们现在拿到的没有区别。
2. **它给的坐标不是我们要的坐标。** 元素之所以是元素，是因为镜头能对准它。
   docling 给的是 PDF 点、左下原点，而导演、高亮框、画面质检全都工作在渲染页
   像素、左上原点里——「能解析出结构」只是半个能力，另外半个仍然要我们写。
3. **成本落在最不该加的地方。** 桌面版运行时已经 400MB，首次下载本来就是安装
   这个应用最慢的一步；再压进一个 torch，为的是在文本密集的页面上拿到*更少*的
   元素（第 1 页 7 对 29，第 8 页 28 对 43）。

什么时候该重新评估：如果输入扩展到扫描件、DOCX，或者遇到确实需要表格结构的
文档——那正是本项目 PDF 侧的真实短板，只是 docling 这次没有补上它。
