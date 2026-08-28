#!/usr/bin/env python3
"""渲染之前把讲稿量一遍。**这一步省不掉**——音频一旦生成，长度就改不动了。

分两类，分得很清楚：

**硬伤**（退出码 1，不改就别渲染）——缺页、超预算、稿子里混进了念不出来的东西。
这三样都会直接毁掉成片：缺页那一页只剩占位文本，超预算成片就超时长，
markdown 标记会被一字一字念出来。

**提醒**（退出码 0）——照读页面、漏讲条目、开场雷同、句子长到字幕装不下。
这些是「不好听」，不是「跑不了」，而且判断权在你：机器只能量出重合度和条目数，
量不出这一页是不是本来就该照念（页面上本来就是口语的短句，照念是对的）。

    python3 scripts/check_script.py --dir <工作目录>
    python3 scripts/check_script.py --dir <工作目录> --strict   # 提醒也算不通过

量不出来的那几类——事实漂移、半句话、行话、页与页接不上——只有读了才能发现，
见 references/script-review.md，那是你自己要过的一遍。
"""

from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from lib.workspace import (  # noqa: E402
    Meta,
    bootstrap,
    chars,
    read_scripts,
    require_engine,
    script_path,
)

#: 超出多少才算超。8% 以内是估算本身的误差，退回去重写反而更不准；
#: 另给 1 秒的绝对余量——封面那种六秒的页面，8% 只有半秒，一个词就破线。
OVER = 1.08
OVER_SLACK = 1.0
#: 少到什么程度算「这一页没讲」。页面文字少的一页本来就该短，所以线放得低。
UNDER = 0.55
#: 整片短到这个程度就是硬伤。要 6 分钟给了 5 分钟，是没交付要的东西——
#: 而且它不是「差一点」：短的那一分钟里，页面上有内容没被讲到。
SHORT = 0.90
#: 照读：讲稿的字二元组有多大比例来自页面。0.8 以上基本是把页面念了一遍。
ECHO = 0.80
#: 一段不带标点的话长到字幕装不下。引擎按标点切字幕，单条上限 34 字——
#: 所以量的是「两个标点之间有多长」，不是整句有多长。
CUE = 34

# 念不出来的东西。写进讲稿就会被一字一字读出来，或者把切句切乱。
FORBIDDEN = [
    (re.compile(r"\*\*|__|`|~~"), "markdown 标记"),
    (re.compile(r"^\s*[#>]", re.M), "markdown 标题/引用"),
    (re.compile(r"^\s*[-*+•]\s", re.M), "列表符号"),
    (re.compile(r"^\s*\d+[.)、]\s", re.M), "序号"),
    (re.compile(r"[\U0001F300-\U0001FAFF☀-➿]"), "emoji"),
    (re.compile(r"（\s*(此处|这里|TODO|待补|待定)|TODO|XXX"), "没写完的占位"),
]


def main() -> int:
    parser = argparse.ArgumentParser(description="渲染前量一遍讲稿")
    parser.add_argument("--dir", required=True, type=Path, help="prepare.py 建的工作目录")
    parser.add_argument("--strict", action="store_true", help="提醒也算不通过")
    args = parser.parse_args()

    work = bootstrap(args.dir)
    require_engine()
    meta = Meta.load(work)

    from doc2video.agent import Doc2VideoAgent
    from doc2video.core.config import get_settings
    from doc2video.skills import NarrationSkill
    from doc2video.skills.base import SkillContext
    from doc2video.skills.review import _overlap_ratio, missed_items, stamped_openings
    from doc2video.tools.tts import TTSTool
    from doc2video.tools.tts.base import estimate_duration

    settings = get_settings()
    agent = Doc2VideoAgent()
    project = agent.store.load(meta.project_id)
    guide = NarrationSkill(SkillContext.build(project, store=agent.store)).guide()
    pace = TTSTool(settings).chars_per_second
    rate = settings.tts_speech_rate or 1.0
    written = read_scripts(work)

    hard: list[str] = []
    soft: list[str] = []
    total = 0.0
    # 每页首尾的留白：成片里有、口播里没有。目标时长说的是成片，所以对目标
    # 之前必须把它加回去——不加的话，一份刚好写满预算的稿子看上去永远「短了
    # 两成」，而按那个数字去加长，成片就真的超了。
    silence = sum(row["page_seconds"] - row["target_seconds"] for row in guide)
    short: list[tuple[float, int]] = []
    previous: tuple[int, str] | None = None

    for row in guide:
        index = row["page"]
        page = project.document.page(index)
        text = written.get(index, "").strip()
        if not text:
            hard.append(f"第 {index} 页没有讲稿——{script_path(work, index)} 还是空的")
            continue

        for pattern, what in FORBIDDEN:
            if pattern.search(text):
                hard.append(f"第 {index} 页混进了{what}——它会被一字一字念出来")
                break

        seconds = estimate_duration(text, rate, pace)
        total += seconds
        target = row["target_seconds"]
        written_chars = chars(text)
        broke, warned = False, False

        if seconds > target * OVER + OVER_SLACK:
            # 按秒折成字，不按字数差——英文按词计时，一页英文多的稿子字数看着
            # 不多、时长已经超了，报字数差会让人往错的方向删。
            cut = max(1, round((seconds - target) * pace))
            hard.append(
                f"第 {index} 页超了：{seconds:.1f} 秒 / 目标 {target} 秒"
                f"（{written_chars} 字，约砍 {cut} 字）"
            )
            broke = True
        elif seconds < target * UNDER:
            soft.append(
                f"第 {index} 页只有 {seconds:.1f} 秒 / 目标 {target} 秒——"
                "页面上还有内容没讲到就补，本来就短就算了"
            )
            warned = True
        if seconds < target:
            short.append((target - seconds, index))

        page_text = "\n".join(e.text for e in page.elements if e.text) if page else ""
        if page_text and _overlap_ratio(text, page_text) >= ECHO:
            soft.append(f"第 {index} 页像是把页面念了一遍（重合度高）——除非页面本来就是口语")
            warned = True
        if page:
            named, affordable, missed = missed_items(text, page)
            if affordable and named < affordable:
                items = "」「".join(" ".join(item.split()) for item in missed[:4])
                soft.append(
                    f"第 {index} 页讲到 {named} 处，这一页的字数够讲 {affordable} 处；"
                    f"漏了「{items}」"
                )
                warned = True
        if stamped := stamped_openings(text):
            soft.append(f"第 {index} 页连着 {len(stamped)} 句一个模子（「名称，动词……」）")
            warned = True
        if long_run := _longest_run(text):
            soft.append(
                f"第 {index} 页有 {len(long_run)} 个字中间没有标点——"
                f"字幕单条最多 {CUE} 字，会溢出：「{long_run[:18]}…」"
            )
            warned = True
        if previous and _same_opening(previous[1], text):
            soft.append(f"第 {previous[0]} 页和第 {index} 页开头是同一个说法")
            warned = True
        previous = (index, text)

        mark = "✗ " if broke else ("! " if warned else "  ")
        print(f"{mark}第 {index:>2} 页  {seconds:5.1f}s / {target:5.1f}s  {written_chars:>4} 字")

    target_total = project.intent.duration
    film = total + silence
    print(
        f"\n口播 {total:.0f} 秒 + 每页首尾留白 {silence:.0f} 秒 ≈ 成片 {film:.0f} 秒"
        f"｜目标 {target_total} 秒（{(film - target_total) / max(target_total, 1) * 100:+.0f}%）"
    )
    # 每页都在余量之内，加起来仍可能超或短——余量是会累积的。
    if film > target_total * 1.05:
        hard.append(
            f"合计超了：预计成片 {film:.0f} 秒 / 目标 {target_total} 秒。"
            "每页都在余量内也会这样，余量是会累积的——挑最长的几页压。"
        )
    elif film < target_total * SHORT:
        hard.append(
            f"合计短了：预计成片 {film:.0f} 秒 / 目标 {target_total} 秒。"
            f"{_where_to_add(short)}"
        )
    elif film < target_total * 0.95:
        soft.append(
            f"比目标短一点：预计成片 {film:.0f} 秒 / 目标 {target_total} 秒。"
            f"{_where_to_add(short)}"
        )

    for line in hard:
        print(f"✗ {line}")
    for line in soft:
        print(f"! {line}")

    if hard:
        print(f"\n{len(hard)} 处硬伤，改完再跑一次。")
        return 1
    if soft:
        print(f"\n{len(soft)} 处提醒。自己判断要不要改——机器量不出这一页该不该照念。")
        return 1 if args.strict else 0
    print("\n可以渲染：python3 scripts/make_video.py --dir <工作目录>")
    return 0


def _where_to_add(short: list[tuple[float, int]]) -> str:
    """去哪儿加：离预算最远的几页，而不是「每页都写长一点」。

    平摊是错的做法——写满了的页面再加就是灌水，而空得最多的那几页往往是页面上
    条目最多、最该讲全的那几页。
    """
    if not short:
        return "所有页都写到预算了——是目标时长本身定得偏长。"
    worst = sorted(short, reverse=True)[:4]
    where = "、".join(f"第 {index} 页少 {gap:.0f} 秒" for gap, index in worst)
    return f"回页面找还没讲到的内容补，先补空得最多的：{where}。"


def _longest_run(text: str) -> str:
    """两个标点之间最长的一段。字幕按标点切，所以这一段就是一条字幕的长度。"""
    longest = max(re.split(r"[，。、！？：；「」\s]+", text), key=len, default="")
    return longest if len(longest) > CUE else ""


def _same_opening(before: str, after: str) -> bool:
    """相邻两页用同一个说法起头。连着几页「这一页是……」听起来就是模板。"""
    head = re.compile(r"[，。！？：；\s]")
    a = head.split(before.strip(), 1)[0][:6]
    b = head.split(after.strip(), 1)[0][:6]
    return len(a) >= 4 and a == b


if __name__ == "__main__":
    raise SystemExit(main())
