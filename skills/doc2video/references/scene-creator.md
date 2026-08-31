# 画面：一场一个组件

配音已经跑完，每一场多长是定死的。你的活是给每一场写一个 Remotion 组件，让画面
在这个长度里说清楚这一场的意思。

## 文件与签名

一场一个文件，写进 `<工作目录>/项目/src/scenes/`：

```tsx
// SceneOO7.tsx —— 文件名和组件名都是 Scene + 三位序号，注册表照这个找
import React from "react";
import { AbsoluteFill, interpolate, spring, useCurrentFrame, useVideoConfig } from "remotion";

import { layout, motion, palette, typography } from "../design-system";
import type { SceneProps } from "../compositions/generated-scenes";

export const Scene007: React.FC<SceneProps> = ({ durationInFrames }) => {
  const frame = useCurrentFrame();
  const { fps } = useVideoConfig();
  // …
  return <AbsoluteFill style={{ padding: layout.margin }}>{/* … */}</AbsoluteFill>;
};
```

`SceneProps` 给两样东西：

- `durationInFrames` —— 这一场有多少帧。**所有动画必须在这个长度内结束。**
- `segments` —— 这一场的讲稿，逐句带时间。用它让画面跟着话走（见「跟着话走」）。

## 六条硬规矩

**一，不画背景。** 纸面由宿主统一画在所有场景底下。场景自己再铺一层全屏底色，
二十场就是二十种米白，片子会看起来像拼的。要强调就画卡片、色块，不要铺满。

**二,不设自己的时长。** 不要 `<Sequence durationInFrames={90}>` 写死，不要假设
「这场大概三秒」。长度是配音量出来的，只能从 `durationInFrames` 读。

**三，颜色只从 `design-system.ts` 取。** `palette.accent` 是全片唯一的强调色，
数据系列按 `palette.series` 的顺序用。不要写 `#FF5733`。

**四，字号只从 `typography.scale` 取。** 舞台是 1920×1080 的设计尺寸，宿主负责
缩放到输出分辨率——所以按设计像素写，不要算百分比，也不要管 4K。

**五，不放字幕。** 宿主会画。场景里再写一遍就是两行字叠在一起。

**六，不读文件。** 不要 `staticFile` 去拿文档页面图——那正是这一版要摆脱的东西。
画面里的一切都是画出来的。

## 跟着话走

`segments` 是这一场每句话的时间。让元素在它被讲到的那一刻出现，是这类片子最值钱
的一件事：

```tsx
const spoken = (index: number) => {
  const seg = segments[index];
  if (!seg) return 0;
  return interpolate(
    frame / fps,
    [seg.start - 0.15, seg.start + 0.25],
    [0, 1],
    { extrapolateLeft: "clamp", extrapolateRight: "clamp" },
  );
};
```

三个要点配三句话，第 n 个要点在第 n 句被念到时亮起来。不要一上来全亮——那样
画面在头一秒就讲完了，剩下二十秒观众在等。

`segments` 可能是空的（引擎没报时间时会退化）。**写的时候要能兜住**：拿不到就
按 `durationInFrames` 均分。

## 画什么

按这一场的 `visual` 和 `keyPoints` 来。常用的几种：

| 这一场在说 | 画成 |
| --- | --- |
| 一个判断、一句主张 | 大字落下 + 一句副题 |
| 几件并列的事 | 卡片依次进场，讲到哪张哪张亮 |
| 数字、对比 | 数字滚动到目标值，柱子长出来 |
| 流程、步骤 | 节点从左到右连起来，箭头跟着画 |
| 结构、架构 | 分层的块自下而上堆起来 |
| 转折、递进 | 前一个概念淡出，后一个推入 |

**别画文档。** 不要还原表格的边框、不要复刻组织架构图的方框，不要把一页的
四个板块摆成四个格子。那是把原文档换了个颜色重画一遍。

## 动效

`motion` 里的三个数就够用：`enter` 12 帧进场，`stagger` 4 帧错开，`exit` 8 帧
退场。`motion.spring` 是纸的手感——有阻尼，不弹。

短是有理由的：一场可能只有十秒，一个两秒才落定的动画会吃掉五分之一。

不要用 CSS `transition` 或 `animation`——Remotion 逐帧渲染，CSS 时间轴和它对不上，
表现是渲出来的片子里动画根本没动。一切位置和透明度都必须是 `frame` 的函数。

## 自查

写完一批跑一次类型检查，这是唯一能挡住「渲出来那一场是空白」的东西：

```bash
cd <工作目录>/项目 && npx tsc --noEmit
```

Remotion 里一个类型错误的场景不会让渲染失败，它会让那一场变成空画面——片子
渲成功，中间黑了八秒，而配音还在念。
