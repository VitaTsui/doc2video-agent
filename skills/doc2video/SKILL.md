---
name: doc2video
description: 把 PDF / PPT 变成动画讲解视频。读完材料重新组织成分镜，逐场配播音腔，画面是为每一场生成的 Remotion 动画——不出现文档原文，也不出现它的排版。用户提到把 PPT/PDF 做成视频、讲解视频、动画视频、汇报视频、把方案讲一遍时使用。
---

# 动画讲解视频：PDF / PPT → MP4

一份材料，重新讲一遍。**画面是生成的，不是文档。**

观众看到的是为每一句话专门做的动画——数字滚出来、卡片依次亮起、流程连起来——
文档只是你知道这件事的来源。成片里不会出现原文的段落，也不会出现它的排版。

需要模型的有两步：**把材料总结成分镜**，和**把每一场画出来**。解析、配音、
时间轴、注册、渲染都是确定性的脚本。

配音固定**播音腔 `zh-CN-YunyangNeural`**。

## 和「对着文档讲解」的区别

这个技能包换过一次做法。上一版把每页渲成图，视频就是那张图配镜头运动，讲稿逐页
写、卡字数预算。现在：

| | 上一版 | 现在 |
| --- | --- | --- |
| 画面 | 文档页面截图 + 镜头运动 | 逐场生成的动画组件 |
| 结构 | 一页一场，按页码走 | 按意思分场，页码只用于追溯 |
| 讲稿 | 卡死在每页字数预算内 | 写多长都行，时间轴跟着配音走 |
| 时长 | 按字数估，超了要重写 | 配完音量出来的，不估 |
| 硬依赖 | Python | Python **+ Node 18+** |

**时间轴的方向反了过来**，这是最要紧的一处：先配音，量出每场真正多长，画面再按
这个长度做。上一版「讲稿超预算 → 重写 → 重配」的循环没有了。代价是场景组件必须
在配音之后写。

## 资料路由

只在需要时读：

- **写分镜**：[storyboard.md](./references/storyboard.md)
  —— 怎么把材料重新组织，契约字段，分多少场，A/B 怎么分，讲稿三准则。
  **动笔之前必须读。**
- **写画面**：[scene-creator.md](./references/scene-creator.md)
  —— 组件签名、六条硬规矩、怎么让画面跟着话走、画什么、动效。
  **写第一个场景之前必须读。**
- **B-roll**：[broll-director.md](./references/broll-director.md)
  —— 能力探测、纸艺风格、提示词、长度对不上怎么办。有 B-roll 时读。
- **声音与时长**：[voice-and-timing.md](./references/voice-and-timing.md)
- **沙箱与交付**：[sandbox-and-delivery.md](./references/sandbox-and-delivery.md)

## 开工前

### 1 · 两个引擎

Python 引擎负责解析和配音，Node 负责画面。**两个都要有。**

```bash
pip install <技能包>/vendor/doc2video_agent-*.whl --no-deps \
  && pip install -r <技能包>/requirements.txt \
  && python3 <技能包>/scripts/check_env.py
node --version   # 18+
```

`check_env.py` 会**真合成一句话**试播音腔——装得上和连得通是两件事，而连不通
会在配音那一步才炸，那时材料已经解析完了。

### 2 · 工作目录

任何可写目录都行，硬条件是你和脚本都写得进去。要派子智能体（分批写场景）时才
必须落在 `/workspace/myspace/` 下。

```bash
D=/workspace/讲解视频 && mkdir -p "$D" && test -w "$D" && echo "可写：$D"
```

⚠️ 实测：某些平台的 `/workspace/myspace/` 是 root 属主而 bash 以 ubuntu 跑，
`mkdir` 直接 Permission denied。**不要在这上面耗**，换 `/workspace/<名>`。

### 3 · 没有文字层的 PDF：先 OCR

扫描件、导成图再拼的 PDF——页面上有字但取不出字。`prepare.py` 会当场拒绝。

```bash
pip install rapidocr-onnxruntime
python3 scripts/ocr_pdf.py --in 扫描件.pdf --out 带文字层.pdf --augment
```

`--augment` 连图里的字一起捞（架构图、产品截图里的字是画上去的）。实测一份
30 页方案，第 27 页文字层 243 字、图里另有 1936 字——不捞出来，总结就是照着
四分之一的内容做的。

⚠️ **OCR 会认错字，而错字会被念出去。** 写分镜前扫一眼机构名和数字：实测
「浙江大学」被识成「湘江大学」。画面上是对的、声音是错的，这种错最难发现。

### 4 · PDF 优先

PDF 由 PyMuPDF 解析。PPT 要靠 LibreOffice 才准，沙箱里通常没有。拿到 `.pptx`
先问能不能给导出的 PDF；旧版 `.ppt` 必须先转。

## 执行链路

```
prepare.py           解析文档 → 素材.md                    秒级
init_project.py      复制 Remotion 模板 + npm install      分钟级
   ↓
你写分镜             ← 读 storyboard.md，产出 分镜.json
validate_storyboard.py                                     秒级
   ↓
make_voice.py        逐场配音，量出时间轴 → 配音.json       分钟级
   ↓
你写画面             ← 读 scene-creator.md，一场一个 .tsx
（有 B-roll 就同时按 broll-director.md 生成素材）
   ↓
register_scenes.py   对齐三者，生成注册表                   秒级
render.py            Remotion 渲染                          分钟级，后台
job_status.py        轮询
   ↓
交付 成片.mp4
```

### 1 · 解析

```bash
python3 scripts/prepare.py --file /path/xxx.pdf \
  --brief "8 分钟，面向企业客户，重点讲落地路径" \
  --out /workspace/讲解视频/工作目录
```

产出 `素材.md`：整份材料的内容，按页码留出处。**这是内容来源，不是画面。**

### 2 · 建工程

```bash
python3 scripts/init_project.py --out /workspace/讲解视频/工作目录
```

复制模板并装 Remotion 依赖。装依赖是分钟级的，**和写分镜并行做**——分镜不依赖
它。

### 3 · 写分镜

**先读 [storyboard.md](./references/storyboard.md)。** 通读 `素材.md`，弄清这份
材料在讲什么，然后按讲给人听的顺序重新组织成 `分镜.json`。

写完 `validate_storyboard.py` 自查，它拦得住 id 不连号、单场过长、B-roll 超标。

### 4 · 配音

```bash
python3 scripts/make_voice.py --out <工作目录>
```

逐场合成，量出每场真实长度，写进 `配音.json`。**这一步定死时间轴**，后面的画面
照着它做。

### 5 · 写画面

**先读 [scene-creator.md](./references/scene-creator.md)。** 一场一个组件，写进
`项目/src/scenes/SceneNNN.tsx`。每场多长在 `配音.json` 里。

场景多的时候分批派子智能体，一批 5 场左右。每批写完跑一次：

```bash
cd <工作目录>/项目 && npx tsc --noEmit
```

类型错误在 Remotion 里不会让渲染失败，它会让那一场变成**空白画面**——片子渲
成功，中间黑了八秒，配音还在念。这是这条链路上最容易漏掉的一种坏。

### 6 · 注册与渲染

```bash
python3 scripts/register_scenes.py --out <工作目录>
python3 scripts/render.py --out <工作目录>          # 默认后台
python3 scripts/job_status.py --out <工作目录>      # 轮询
```

`register_scenes.py` 把分镜、配音、场景文件三者对齐，缺哪一场就说哪一场，不会
静默跳过。

### 7 · 改一场

改分镜里那一场的讲稿，然后：

```bash
python3 scripts/make_voice.py --out <工作目录> --scenes 7
python3 scripts/register_scenes.py --out <工作目录>
python3 scripts/render.py --out <工作目录>
```

只有第 7 场重配，但它变长变短会让后面所有场次的起点跟着移——所以注册表必须
重新生成，渲染也是整片重来。只改画面不改讲稿的话，跳过配音那一步。

## 关键约束

- **画面里不出现文档原文。** 不搬段落、不复刻排版、不还原表格边框。素材是依据，
  不是要照搬的东西。
- **分镜是你写的，画面是你写的。** 引擎里没有模型，也不配任何模型 Key。
- **时长不用估。** 讲稿写多长都行，时间轴是配完音量出来的。但一场超过 25 秒
  说明里面不止一件事，该拆。
- **场景不设自己的时长。** 只能从 `durationInFrames` 读。
- **颜色和字号只从 `design-system.ts` 取。** 二十场各自挑颜色，片子会像拼的。
- **渲染是分钟级的，默认后台跑。** 前台必被工具预算打断在中途。
- **不要替材料编内容。** 材料没写的原因、影响、预测，讲出来就是错的，而它会配
  着一个煞有介事的画面播出去。
