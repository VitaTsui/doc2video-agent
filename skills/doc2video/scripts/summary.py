#!/usr/bin/env python3
"""出片之后，这支视频到底是什么样。

三块，按「要不要重做」的顺序排：

1. **降级记录**——跑完了但打了折的地方。哪一页没讲稿用了占位、配音落到了别的
   引擎、字幕没烧上……这些都不会让运行失败，所以不看就不知道。
2. **时长**——实际对目标。差得多就是有几页写长了或写短了。
3. **逐场景讲稿**——成片里真正念出来的那份文字。你自己读一遍，
   要改哪页就改 ``讲稿/pNN.md``，再 ``make_video.py --pages N``。

    python3 scripts/summary.py --dir <工作目录>
    python3 scripts/summary.py --dir <工作目录> --full   # 讲稿打全文，不截断
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from lib.workspace import VIDEO, Meta, bootstrap, read_status, require_engine  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser(description="看这支视频是什么样")
    parser.add_argument("--dir", required=True, type=Path)
    parser.add_argument("--full", action="store_true", help="讲稿打全文")
    args = parser.parse_args()

    work = bootstrap(args.dir)
    require_engine()
    meta = Meta.load(work)

    from doc2video.agent import Doc2VideoAgent

    agent = Doc2VideoAgent()
    project = agent.store.load(meta.project_id)
    status = read_status(work)

    print(
        f"工程 {project.project_id}｜{project.document.title or meta.source}\n"
        f"来源 {project.source.file}｜状态 {project.status.value}｜配音 {project.intent.voice}"
    )

    telemetry = project.telemetry
    if telemetry and telemetry.degradations:
        print("\n降级（跑完了，但打了折）：")
        for item in telemetry.degradations:
            print(f"  ! {item.what}：{item.reason}")
    elif telemetry:
        print("\n降级：无")

    actual = project.total_duration()
    target = project.intent.duration
    drift = (actual - target) / target * 100 if target else 0.0
    print(
        f"\n时长 {actual:.1f} 秒 / 目标 {target} 秒（{drift:+.0f}%）"
        f"｜{len(project.scenes)} 个场景"
    )
    if telemetry:
        print(f"上次运行 {telemetry.duration_s:.0f} 秒")

    video = work / VIDEO
    final = agent.store.resolve(project.project_id, project.render.output_path)
    print(f"成片 {video}" if video.exists() else f"成片 {final or '（未生成）'}")
    if status.get("state") == "running":
        print("! 还有一个渲染在跑，下面这些是它跑完之前的状态")

    print("\n逐场景：")
    for scene in project.scenes:
        text = scene.narration if args.full else scene.narration[:60] + (
            "…" if len(scene.narration) > 60 else ""
        )
        print(f"  {scene.scene_id}  p{scene.source_page:<3} {scene.duration:6.1f}s  {text}")

    print(
        "\n要改哪一页：改 讲稿/pNN.md，然后\n"
        f"  python3 scripts/make_video.py --dir {work} --pages N\n"
        "只有那一页会重新配音、重渲，其余片段原样复用。"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
