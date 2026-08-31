#!/usr/bin/env python3
"""出片之后看一眼：几场、多长、每场讲了什么、渲成了没有。

上一版这里读的是引擎的 telemetry，因为整条链路都在引擎里跑。这一版引擎只管
解析和配音，画面和渲染都在 Remotion 那边——所以摘要改成读工作目录自己的三份
文件，它们才是这支片子的事实。

交给用户之前跑一次。它会说出你自己不一定会去查的两件事：实际时长和目标差多少，
以及有没有哪一场长得离谱。
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from lib.workspace import (  # noqa: E402
    VIDEO,
    Meta,
    bootstrap,
    load_storyboard,
    load_voicemap,
    read_status,
)

# 超过这个长度的一场，多半是当初该拆没拆。不是错，是提醒。
LONG_SCENE_SECONDS = 25.0


def main() -> int:
    parser = argparse.ArgumentParser(description="成片摘要")
    parser.add_argument("--out", required=True, type=Path, help="工作目录")
    parser.add_argument("--full", action="store_true", help="连每场讲稿一起打印")
    args = parser.parse_args()

    work = bootstrap(args.out)
    meta = Meta.load(work)
    board = load_storyboard(work)
    voicemap = load_voicemap(work)

    scenes = board["scenes"]
    timing = {row["id"]: row for row in voicemap["scenes"]}
    total = voicemap["total"]

    print(f"《{board.get('title') or meta.source}》")
    print(f"  来源 {meta.source}｜要求：{meta.brief}")
    print(f"  {len(scenes)} 场，{total:.1f} 秒（{total / 60:.1f} 分钟）｜配音 {voicemap['voice']}")

    a_roll = sum(1 for s in scenes if s.get("rollType", "a-roll") == "a-roll")
    if a_roll < len(scenes):
        share = (total - sum(timing[s["id"]]["duration"] for s in scenes
                             if s.get("rollType") == "a-roll")) / total if total else 0
        print(f"  A-roll {a_roll} 场，B-roll {len(scenes) - a_roll} 场（占 {share:.0%}）")

    print()
    long_ones = []
    for index, scene in enumerate(scenes, start=1):
        row = timing.get(scene["id"])
        if row is None:
            print(f"  {index:>2}. {scene['id']} —— 没有配音")
            continue
        seconds = row["duration"]
        mark = "！" if seconds > LONG_SCENE_SECONDS else " "
        roll = "B" if scene.get("rollType") == "b-roll" else "A"
        print(
            f"  {index:>2}.{mark}[{roll}] {row['start']:>6.1f}s +{seconds:>5.1f}s  "
            f"{scene.get('beat') or scene['id']}"
        )
        if args.full:
            print(f"        {scene.get('narration', '').strip()}")
        if seconds > LONG_SCENE_SECONDS:
            long_ones.append((index, seconds))

    if long_ones:
        print(f"\n！{len(long_ones)} 场超过 {LONG_SCENE_SECONDS:.0f} 秒：")
        for index, seconds in long_ones:
            print(f"    第 {index} 场 {seconds:.1f} 秒——一场里多半不止一件事")

    status = read_status(work)
    video = work / VIDEO
    print()
    if video.exists():
        size = video.stat().st_size / 1024 / 1024
        print(f"成片 {video}（{size:.1f} MB）")
        if elapsed := status.get("elapsed"):
            print(f"  渲染用了 {elapsed:.0f} 秒")
    elif status.get("state") == "running":
        print("还在渲——python3 scripts/job_status.py --out <工作目录>")
    elif error := status.get("error"):
        print(f"渲染失败：{error}")
    else:
        print("还没渲——python3 scripts/render.py --out <工作目录>")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
