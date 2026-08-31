#!/usr/bin/env python3
"""分镜自查：结构对不对，长度离不离谱。

只查机器查得出来的。查不出来的那件事——「这支片子讲下来是不是一个人在把一件
事讲清楚」——得自己读一遍，`storyboard.md` 末尾说了。

这里的秒数是**估**的，按播音腔 4.45 字/秒。真实时长要等 make_voice.py 量。
估在这里的意义是：一场估出来 40 秒，那它铁定该拆，不值得先花一分钟配音再发现。
"""

from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from lib.workspace import bootstrap, chars, load_storyboard  # noqa: E402

CHARS_PER_SECOND = 4.45
SCENE_SECONDS_MIN = 8
SCENE_SECONDS_MAX = 25
BROLL_SHARE_MAX = 0.15
BROLL_SECONDS_MAX = 8


def main() -> int:
    parser = argparse.ArgumentParser(description="检查分镜")
    parser.add_argument("--out", required=True, type=Path, help="工作目录")
    args = parser.parse_args()

    work = bootstrap(args.out)
    board = load_storyboard(work)
    scenes = board.get("scenes", [])

    errors: list[str] = []
    warnings: list[str] = []

    if not scenes:
        raise SystemExit("分镜里一场都没有")

    seconds: list[float] = []
    for index, scene in enumerate(scenes, start=1):
        want = f"scene-{index:03d}"
        got = scene.get("id")
        if got != want:
            errors.append(f"第 {index} 场的 id 是 {got!r}，应该是 {want!r}——id 必须连号")

        roll = scene.get("rollType", "a-roll")
        if roll not in ("a-roll", "b-roll"):
            errors.append(f"{got}：rollType 只能是 a-roll 或 b-roll，不是 {roll!r}")

        narration = (scene.get("narration") or "").strip()
        if not narration:
            errors.append(f"{got}：没有讲稿。B-roll 也要有——它只是画面不同，不是不说话")
            seconds.append(0.0)
            continue

        estimate = chars(narration) / CHARS_PER_SECOND
        seconds.append(estimate)

        if estimate > SCENE_SECONDS_MAX:
            errors.append(
                f"{got}：约 {estimate:.0f} 秒，超过 {SCENE_SECONDS_MAX} 秒。"
                "一场里不止一件事，拆开"
            )
        elif estimate < SCENE_SECONDS_MIN and roll == "a-roll":
            warnings.append(f"{got}：约 {estimate:.0f} 秒，偏短——观众刚看懂画面就切走了")

        if roll == "b-roll" and estimate > BROLL_SECONDS_MAX:
            errors.append(f"{got}：B-roll 约 {estimate:.0f} 秒，超过 {BROLL_SECONDS_MAX} 秒")

        if not scene.get("visual", "").strip():
            warnings.append(f"{got}：没写 visual——画面要做成什么样，写场景的人只能靠猜")
        if not scene.get("keyPoints"):
            warnings.append(f"{got}：没写 keyPoints，这一场要让人记住什么？")

        if _looks_copied(narration):
            warnings.append(
                f"{got}：讲稿里有书面语标记（编号、括注、项目符号），"
                "像是从材料里搬的——念出来会很奇怪"
            )

    total = sum(seconds)
    broll = sum(s for s, scene in zip(seconds, scenes) if scene.get("rollType") == "b-roll")
    if total and broll / total > BROLL_SHARE_MAX:
        errors.append(
            f"B-roll 占 {broll / total:.0%}，超过 {BROLL_SHARE_MAX:.0%}——"
            "它是气口，不是内容"
        )

    for line in errors:
        print(f"✗ {line}", file=sys.stderr)
    for line in warnings:
        print(f"! {line}", file=sys.stderr)

    print(
        f"\n{len(scenes)} 场，估计 {total:.0f} 秒（{total / 60:.1f} 分钟）"
        f"｜B-roll {sum(1 for s in scenes if s.get('rollType') == 'b-roll')} 场"
        f"\n{len(errors)} 个错，{len(warnings)} 条提醒"
    )
    if errors:
        print("\n改完再跑一次。", file=sys.stderr)
        return 1
    print("\n可以配音了：python3 scripts/make_voice.py --out <工作目录>")
    return 0


def _looks_copied(text: str) -> bool:
    """像是从材料里搬过来的，而不是写出来念的。"""
    return bool(
        re.search(r"[（(][^）)]{6,}[）)]", text)  # 长括注
        or re.search(r"^\s*[·•▪]\s*", text, re.M)  # 项目符号
        or re.search(r"[一二三四五六七八九十]、", text)  # 公文编号
        or re.search(r"\d+\.\d+\.\d+", text)  # 章节号
    )


if __name__ == "__main__":
    raise SystemExit(main())
