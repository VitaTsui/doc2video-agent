#!/usr/bin/env python3
"""把分镜、配音和写好的场景拼成注册表——Remotion 唯一读的那份清单。

注册表是**生成**的，不是手写的。它把三样东西对齐：分镜说每场是 A 还是 B，
配音说每场多长、从第几秒开始，场景目录说画面在哪个组件里。任何一样对不上，
这一步就停下来说是哪一场对不上——比渲染出一支中间黑掉一段的片子好。

每次跑都整份重写。改一场之后时间轴会整体前后移动，只补一行是不够的。
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from lib.workspace import (  # noqa: E402
    bootstrap,
    load_storyboard,
    load_voicemap,
    project_dir,
    public_dir,
    scene_component,
    scenes_dir,
)

REGISTRY = Path("src") / "compositions" / "generated-scenes.ts"


def main() -> int:
    parser = argparse.ArgumentParser(description="生成场景注册表")
    parser.add_argument("--out", required=True, type=Path, help="工作目录")
    args = parser.parse_args()

    work = bootstrap(args.out)
    board = load_storyboard(work)
    voicemap = load_voicemap(work)

    timing = {row["id"]: row for row in voicemap["scenes"]}
    rows, problems = [], []

    for scene in board["scenes"]:
        scene_id = scene["id"]
        voiced = timing.get(scene_id)
        if voiced is None:
            problems.append(f"{scene_id} 没有配音——跑一次 make_voice.py")
            continue

        roll = scene.get("rollType", "a-roll")
        if roll == "b-roll":
            source = scene.get("brollSrc") or f"broll/{scene_id}.mp4"
            media = public_dir(work) / source
            if not media.exists():
                problems.append(f"{scene_id} 是 b-roll，但素材不在：{media}")
                continue
            rows.append({**voiced, "rollType": "b-roll", "videoSrc": source,
                         "mediaDuration": _media_seconds(media)})
        else:
            component = scene_component(scene_id)
            path = scenes_dir(work) / f"{component}.tsx"
            if not path.exists():
                problems.append(f"{scene_id} 的画面还没写：{path}")
                continue
            rows.append({**voiced, "rollType": "a-roll", "component": component})

    if problems:
        print("对不上的地方：", file=sys.stderr)
        for line in problems:
            print(f"  - {line}", file=sys.stderr)
        raise SystemExit(1)

    target = project_dir(work) / REGISTRY
    target.write_text(_render(rows, voicemap["total"]), encoding="utf-8")

    a_roll = sum(1 for r in rows if r["rollType"] == "a-roll")
    print(
        f"注册 {len(rows)} 场（A-roll {a_roll}，B-roll {len(rows) - a_roll}）"
        f"，共 {voicemap['total']:.1f} 秒\n→ {target}"
    )
    return 0


def _media_seconds(path: Path) -> float:
    """B-roll 素材真正多长。量不出来就当 0——宿主会按槽位长度铺满。"""
    try:
        out = subprocess.run(  # noqa: S603
            ["ffprobe", "-v", "error", "-show_entries", "format=duration",
             "-of", "default=nw=1:nk=1", str(path)],
            capture_output=True, text=True, timeout=30,
        )
        return round(float(out.stdout.strip()), 3)
    except (OSError, ValueError, subprocess.SubprocessError):
        print(f"⚠️ 量不出 {path.name} 的时长，按槽位铺", file=sys.stderr)
        return 0.0


def _render(rows: list[dict], total: float) -> str:
    imports = [
        f'import {{ {row["component"]} }} from "../scenes/{row["component"]}";'
        for row in rows
        if row["rollType"] == "a-roll"
    ]
    body = ",\n".join(_entry(row) for row in rows)
    return f"""/**
 * 由 scripts/register_scenes.py 生成。**不要手改**——下一次跑会整份覆盖。
 *
 * 时间轴是量出来的：每一场的 start 和 duration 都来自它自己那段配音的实际
 * 长度，没有一个数是估的，也没有一场可以自己决定要多久。
 */
import type React from "react";

{chr(10).join(imports)}

export type SceneSegment = {{
  text: string;
  start: number;
  end: number;
}};

export type SceneProps = {{
  segments: SceneSegment[];
  durationInFrames: number;
}};

type SceneBase = {{
  id: string;
  start: number;
  duration: number;
  audioSrc: string;
  segments: SceneSegment[];
}};

export type GeneratedSceneItem =
  | (SceneBase & {{ rollType: "a-roll"; Component: React.FC<SceneProps> }})
  | (SceneBase & {{ rollType: "b-roll"; videoSrc: string; mediaDuration: number }});

export const generatedScenes: GeneratedSceneItem[] = [
{body}
];

export const totalDuration = {total};
"""


def _entry(row: dict) -> str:
    common = (
        f'    id: "{row["id"]}",\n'
        f'    start: {row["start"]},\n'
        f'    duration: {row["duration"]},\n'
        f'    audioSrc: "{row["audio"]}",\n'
        f"    segments: {json.dumps(row['segments'], ensure_ascii=False)},\n"
    )
    if row["rollType"] == "b-roll":
        tail = (
            '    rollType: "b-roll",\n'
            f'    videoSrc: "{row["videoSrc"]}",\n'
            f'    mediaDuration: {row["mediaDuration"]},\n'
        )
    else:
        tail = '    rollType: "a-roll",\n' f'    Component: {row["component"]},\n'
    return "  {\n" + common + tail + "  }"


if __name__ == "__main__":
    raise SystemExit(main())
