#!/usr/bin/env python3
"""解析文档，把里面的内容摊成一份素材，供你重新组织成分镜。

和这个技能包的上一版有一处根本不同：**页面不再是画面**。

上一版把每页渲成图，视频就是那张图配上镜头运动，讲稿逐页写。这一版的画面是
生成的动画场景，页面只作为**内容来源**——所以这里产出的是「素材.md」而不是
「页面.md」，页码留在里面只为了追溯出处，不代表成片会按页走。

也因此这里不再算逐页预算。一场讲多久由分镜决定，而分镜是按意思分的，不是按
页分的：三页讲同一件事就并成一场，一页里有两件事就拆成两场。
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from lib.workspace import (  # noqa: E402
    MATERIAL,
    STORYBOARD,
    VOICE,
    Meta,
    bootstrap,
    require_engine,
)

# 一场的合理长度。短于下限的场景切得太碎——观众刚看懂画面就切走了；长于上限
# 的一场里必然不止一件事，那是两场。分镜自己定场数，这两个数只是护栏。
SCENE_SECONDS_MIN = 8
SCENE_SECONDS_MAX = 25


def main() -> int:
    parser = argparse.ArgumentParser(description="解析文档，摊出写分镜要用的素材")
    parser.add_argument("--file", required=True, type=Path, help="PDF / PPT / PPTX")
    parser.add_argument("--brief", required=True, help="一句话：多长、给谁看、重点是什么")
    parser.add_argument("--out", required=True, type=Path, help="工作目录（给目录，不是文件名）")
    args = parser.parse_args()

    if args.out.exists() and args.out.is_file():
        raise SystemExit(f"--out 要给一个目录，{args.out} 是文件")
    source = args.file.expanduser().resolve()
    if not source.exists():
        raise SystemExit(f"找不到 {source}")

    work = bootstrap(args.out)
    require_engine()

    from doc2video.agent import Doc2VideoAgent

    agent = Doc2VideoAgent()
    print(f"解析 {source.name} …", file=sys.stderr)
    project = agent.prepare(source, args.brief)
    project.intent.voice = VOICE
    agent.store.save(project)

    pages = project.document.pages
    written = sum(len(p.raw_text().strip()) for p in pages)
    if not written:
        # 引擎自己也会拦，但那是在跑整条链路的时候。在这里说，省下后面所有步骤。
        raise SystemExit(
            f"这份文档 {len(pages)} 页，一个字都没解析出来——每页都是一整张图片"
            "（扫描件，或把幻灯片导成图再拼的 PDF）。\n"
            "先补一层文字：\n"
            "    pip install rapidocr-onnxruntime\n"
            f"    python3 scripts/ocr_pdf.py --in {source.name} --out 带文字层.pdf --augment\n"
            "再拿 --out 出来的那份跑这一步。"
        )

    duration = project.intent.duration
    _write_material(work, project, args.brief, duration)

    Meta(
        project_id=project.project_id,
        source=source.name,
        brief=args.brief,
        voice=VOICE,
    ).save(work)

    low = max(3, round(duration / SCENE_SECONDS_MAX))
    high = max(low, round(duration / SCENE_SECONDS_MIN))
    print(
        f"\n工程 {project.project_id}｜{project.document.title or source.name}\n"
        f"{len(pages)} 页素材，共 {written} 字｜目标时长 {duration} 秒\n"
        f"配音 {VOICE}（播音腔）\n\n"
        f"读 {work / MATERIAL}，按 references/storyboard.md 写出 {work / STORYBOARD}。\n"
        f"这个长度大约 {low}–{high} 场——**场数由内容定，不要凑数**。\n\n"
        "⚠️ 画面里不会出现文档原文。素材是你重新讲这件事的依据，不是要照搬的东西。"
    )
    return 0


def _write_material(work: Path, project, brief: str, duration: int) -> None:
    """素材：文档说了什么，按页码留出处。

    元素文字要给全。只给标题的话，总结出来的是目录——每场一句正确的废话。
    """
    document = project.document
    lines = [
        f"# 素材：{document.title or '未命名'}",
        "",
        f"- 来源 {project.source.file}，共 {len(document.pages)} 页",
        f"- 要求：{brief}",
        f"- 目标时长：{duration} 秒",
        "",
        "> 这是**内容来源**，不是画面。成片里不会出现这些页面，也不会出现它们的排版。",
        "> 页码只用来追溯出处：分镜里写 `sourcePages`，是为了让后面能查证某句话",
        "> 从哪来的，不是说那一场要长得像那一页。",
        "",
    ]
    if document.summary:
        lines += ["## 整份材料在讲什么", "", document.summary, ""]
    if document.key_concepts:
        lines += ["## 关键概念", "", "、".join(document.key_concepts), ""]
    if document.sections:
        lines += ["## 原文的分块", ""]
        for section in document.sections:
            span = ", ".join(str(i) for i in section.page_indexes)
            lines.append(f"- **{section.title}**（第 {span} 页）{section.summary or ''}")
        lines.append("")

    lines += ["## 逐页内容", ""]
    for page in document.pages:
        text = page.raw_text().strip()
        if not text:
            continue
        kind = getattr(page.page_type, "value", page.page_type)
        lines.append(f"### 第 {page.index} 页｜{page.title or '（无标题）'}｜{kind}")
        if page.summary:
            lines.append(f"_{page.summary}_")
        lines.append("")
        for element in page.elements:
            if body := (element.text or "").strip():
                lines.append(f"- {body}")
        lines.append("")

    (work / MATERIAL).write_text("\n".join(lines), encoding="utf-8")


if __name__ == "__main__":
    raise SystemExit(main())
