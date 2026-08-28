#!/usr/bin/env python3
"""解析文档，建工作目录，把「写讲稿要知道的一切」摊在磁盘上。

秒级，不配音不渲染。产出三样：

- ``页面.md``——逐页的标题、类型、页面上的文字与元素。**写讲稿只读这一份。**
- ``预算.tsv``——每页多少秒、多少字。时长是按字数估的，超了成片就超时长，
  而音频一旦生成，长度就改不动了——所以预算要在写之前拿到，不是写完再对。
- ``讲稿/p01.md …``——一页一个空文件，头一行注释写着这页的预算。你往里写。

    python3 scripts/prepare.py --file <PDF/PPTX> --brief "一句话说清要什么" \\
        --out /workspace/myspace/<工作目录>

``--brief`` 里说时长和重点页是有用的：「8 分钟」「第 5 到 8 页重点讲」会被读进
预算里，其余的话不影响预算，但会记进工程。
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from lib.workspace import (  # noqa: E402
    BUDGET,
    PAGES,
    SCRIPTS,
    VOICE,
    Meta,
    bootstrap,
    require_engine,
    script_path,
)


def main() -> int:
    parser = argparse.ArgumentParser(description="解析文档并建出写讲稿要用的工作目录")
    parser.add_argument("--file", required=True, type=Path, help="PDF / PPT / PPTX")
    parser.add_argument("--brief", required=True, help="一句话：多长、给谁看、哪几页重点")
    parser.add_argument("--out", required=True, type=Path, help="工作目录（给目录，不是文件名）")
    args = parser.parse_args()

    if args.out.exists() and args.out.is_file():
        raise SystemExit(f"--out 要给一个目录，{args.out} 是文件")
    source = args.file.expanduser().resolve()
    if not source.exists():
        raise SystemExit(
            f"找不到 {source}。\n"
            "沙箱里材料必须在 /workspace/myspace/ 下——别处的路径子智能体也读不到。"
        )

    work = bootstrap(args.out)
    require_engine()

    from doc2video.agent import Doc2VideoAgent
    from doc2video.skills import NarrationSkill
    from doc2video.skills.base import SkillContext

    agent = Doc2VideoAgent()
    print(f"解析 {source.name} …", file=sys.stderr)
    project = agent.prepare(source, args.brief)

    # 音色写进工程，而不是只留在环境变量里：这支视频是用哪个声音做的，应该由
    # 它自己记着——将来在别的机器上重渲，声音不该跟着那台机器变。
    project.intent.voice = VOICE
    agent.store.save(project)

    guide = NarrationSkill(SkillContext.build(project, store=agent.store)).guide()
    budgets = {row["page"]: row for row in guide}

    _write_pages(work, project, budgets)
    _write_budget(work, guide)
    made = _write_templates(work, project, budgets)

    Meta(
        project_id=project.project_id,
        source=source.name,
        brief=args.brief,
        voice=VOICE,
    ).save(work)

    spoken = sum(row["target_seconds"] for row in guide)
    onscreen = sum(row["page_seconds"] for row in guide)
    print(
        f"\n工程 {project.project_id}｜{project.document.title or source.name}\n"
        f"{len(guide)} 页可讲｜目标 {project.intent.duration} 秒"
        f"（口播 {spoken:.0f} 秒 + 页面首尾留白，成片约 {onscreen:.0f} 秒）\n"
        f"配音 {VOICE}（播音腔）\n\n"
        f"读 {work / PAGES}，按 {work / BUDGET} 的字数上限，"
        f"逐页写进 {work / SCRIPTS}/（新建 {made} 个空模板）。\n"
        f"**先写 3 页就跑一次 check_script.py 校准手感**，再写剩下的——"
        "一次写完再回头压是最贵的顺序。"
    )
    return 0


def _write_pages(work: Path, project, budgets: dict[int, dict]) -> None:
    """页面内容。元素文字要全给——只给标题的话，写出来的是目录不是讲稿。"""
    lines = [
        f"# {project.document.title or project.source.file}",
        "",
        f"主题：{project.document.topic or '（未识别）'}｜"
        f"来源 {project.source.file}｜{len(project.document.pages)} 页",
        "",
        "> 每一页的讲稿写进 `讲稿/pNN.md`。**字数上限见每页的标题行**，超了成片就超时长。",
        "",
    ]
    for page in project.document.ordered_pages():
        budget = budgets.get(page.index)
        head = f"## 第 {page.index} 页｜{page.title or '（无标题）'}"
        if budget:
            head += (
                f"｜{budget['page_type']}｜{budget['target_seconds']} 秒"
                f" / {budget['target_chars']} 字"
            )
        else:
            head += "｜（不讲这一页）"
        lines.append(head)
        if page.summary:
            lines.append(f"摘要：{page.summary}")
        lines.append("")
        texts = [e for e in page.elements if (e.text or "").strip()]
        if not texts:
            lines += ["（这一页没有可提取的文字）", ""]
            continue
        for element in texts:
            # 元素内部的换行是一条一条的条目，不能并成一行。并了之后
            # 「产品定位 系统架构 核心指标」看起来像一句话，而它是三处内容——
            # 讲稿漏掉其中两处，画面上那两处仍在观众眼前。
            rows = [" ".join(row.split()) for row in element.text.splitlines() if row.strip()]
            lines.append(f"- [{element.kind.value}] {rows[0]}")
            lines += [f"  - {row}" for row in rows[1:]]
        lines.append("")
    (work / PAGES).write_text("\n".join(lines), encoding="utf-8")


def _write_budget(work: Path, guide: list[dict]) -> None:
    rows = ["页码\t标题\t页面类型\t口播秒数\t目标字数\t页面在屏秒数"]
    for row in guide:
        rows.append(
            f"{row['page']}\t{row['title']}\t{row['page_type']}\t"
            f"{row['target_seconds']}\t{row['target_chars']}\t{row['page_seconds']}"
        )
    (work / BUDGET).write_text("\n".join(rows) + "\n", encoding="utf-8")


def _write_templates(work: Path, project, budgets: dict[int, dict]) -> int:
    """一页一个空文件。已经写过的不动——重跑 prepare 不该抹掉写好的稿子。"""
    (work / SCRIPTS).mkdir(exist_ok=True)
    made = 0
    for page in project.document.ordered_pages():
        budget = budgets.get(page.index)
        if budget is None:
            continue
        path = script_path(work, page.index)
        if path.exists():
            continue
        path.write_text(
            f"<!-- 第 {page.index} 页｜{budget['title'] or '（无标题）'}"
            f"｜{budget['target_seconds']} 秒｜不超过 {budget['target_chars']} 字 -->\n",
            encoding="utf-8",
        )
        made += 1
    return made


if __name__ == "__main__":
    raise SystemExit(main())
