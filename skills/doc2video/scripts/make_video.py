#!/usr/bin/env python3
"""按你写的讲稿配音、设计镜头、编时间轴、渲染合成。

默认**后台跑**并立刻返回：一份三十页的 deck 要好几分钟，而工具调用普遍有一两
分钟的预算，前台跑必定被打断在中途。轮询用 ``job_status.py``。

    python3 scripts/make_video.py --dir <工作目录>              # 整片
    python3 scripts/make_video.py --dir <工作目录> --pages 3,7  # 只重做这两页
    python3 scripts/make_video.py --dir <工作目录> --foreground # 就地跑（小 deck 或调试）

``--pages`` 是改稿之后用的：改完 ``讲稿/p03.md``，只有第 3 页会重新配音、重渲，
其余片段原样复用。改了哪几页就写哪几页——把全部页码都写上等于整片重做。

配音固定是播音腔 ``zh-CN-YunyangNeural``。**引擎不可用时这里直接拒绝开工**，
不会悄悄换一个本地音色接着跑：换了音色，这份按 4.45 字/秒算出来的讲稿预算
也就不作数了，而成片听起来「还行」，没人会去查它为什么变了。
"""

from __future__ import annotations

import argparse
import os
import shutil
import subprocess
import sys
import time
import traceback
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from lib.workspace import (  # noqa: E402
    LOG,
    VIDEO,
    VOICE,
    Meta,
    bootstrap,
    read_scripts,
    read_status,
    require_engine,
    write_status,
)


def main() -> int:
    parser = argparse.ArgumentParser(description="按讲稿出片")
    parser.add_argument("--dir", required=True, type=Path, help="prepare.py 建的工作目录")
    parser.add_argument("--pages", default="", help="只重做这几页，逗号分隔，如 3,7")
    parser.add_argument("--foreground", action="store_true", help="就地跑，不放后台")
    parser.add_argument(
        "--allow-fallback-voice",
        action="store_true",
        help="播音腔不可用时也照跑（成片会是另一个声音）",
    )
    args = parser.parse_args()

    work = bootstrap(args.dir)
    require_engine()
    Meta.load(work)  # 没 prepare 过就在这里报错，别等到后台里再失败

    pages = [int(p) for p in args.pages.replace("，", ",").split(",") if p.strip()]
    _refuse_if_running(work)
    _check_voice(allow_fallback=args.allow_fallback_voice)

    if args.foreground:
        return _run(work, pages)

    log = (work / LOG).open("a", encoding="utf-8")
    child = subprocess.Popen(
        [sys.executable, __file__, "--dir", str(work), "--foreground"]
        + (["--pages", args.pages] if args.pages else [])
        + (["--allow-fallback-voice"] if args.allow_fallback_voice else []),
        stdout=log,
        stderr=log,
        # 自己一个进程组：父进程（工具调用）结束时不该把渲染一起带走。
        start_new_session=True,
        env=os.environ.copy(),
    )
    write_status(
        work,
        state="running",
        pid=child.pid,
        stage="启动",
        detail="",
        pages=pages,
        started=time.time(),
        updated=time.time(),
        error="",
    )
    print(
        f"已在后台开始（pid {child.pid}）。日志 {work / LOG}\n"
        f"轮询：python3 scripts/job_status.py --dir {work}"
    )
    return 0


def _refuse_if_running(work: Path) -> None:
    status = read_status(work)
    if status.get("state") != "running":
        return
    pid = status.get("pid")
    # 后台那一支是自己被自己派出去的：父进程写完 pid 就退出，子进程起来读到
    # 的正是自己的 pid。不排掉这一条，后台模式一次都跑不起来——踩过。
    if pid == os.getpid():
        return
    alive = False
    if isinstance(pid, int):
        try:
            os.kill(pid, 0)
            alive = True
        except OSError:
            alive = False
    if alive:
        raise SystemExit(
            f"已经有一个渲染在跑（pid {pid}）。先看 job_status.py，"
            "或者确认它死了再重来——两个渲染写同一个工程会互相覆盖。"
        )


def _check_voice(*, allow_fallback: bool) -> None:
    from doc2video.tools.tts.providers import resolve_provider

    provider = resolve_provider("edge")
    if provider.name == "edge":
        return
    reason = getattr(type(provider)(), "unavailable_reason", lambda: "")()
    message = (
        f"播音腔不可用（当前会落到 {provider.name}）：{reason or '未安装 edge-tts'}\n"
        "    pip install edge-tts，并确认沙箱能连外网；\n"
        "    先跑 python3 scripts/check_env.py 看试音过不过。\n"
        "    真要用别的声音出片，加 --allow-fallback-voice。"
    )
    if not allow_fallback:
        raise SystemExit(message)
    print(f"! {message}", file=sys.stderr)


def _run(work: Path, pages: list[int]) -> int:
    """真正干活的那一支。进程一直活到渲染结束，状态写在 状态.json 里。"""
    from doc2video.agent import Doc2VideoAgent

    meta = Meta.load(work)
    written = read_scripts(work)
    if not written:
        raise SystemExit(f"{work / '讲稿'} 里一份讲稿都没有——先写，再渲染")

    agent = Doc2VideoAgent()
    project = agent.store.load(meta.project_id)
    started = time.time()

    def progress(stage: str, message: str, done: int = 0, total: int = 0) -> None:
        write_status(
            work,
            state="running",
            stage=stage,
            detail=message,
            done=done,
            total=total,
            updated=time.time(),
        )
        print(f"  · [{stage}] {done}/{total} {message}" if total else f"  · [{stage}] {message}")

    write_status(work, state="running", stage="开始", started=started, updated=started, error="")
    try:
        if pages:
            # 改页：按页码找场景，只重做这几个。scene_id 是引擎的说法，
            # 页码是人的说法——转换放在这里，别让调用方去记 scene_id。
            by_page = {scene.source_page: scene.scene_id for scene in project.scenes}
            missing = [p for p in pages if p not in by_page]
            if missing:
                raise SystemExit(f"这些页没有对应的场景：{missing}（工程里只有 {sorted(by_page)}）")
            scenes = {by_page[p]: written[p] for p in pages if p in written}
            if len(scenes) != len(pages):
                raise SystemExit(f"这些页没有讲稿：{[p for p in pages if p not in written]}")
            result = agent.run(
                message=f"按调用方讲稿修改第 {pages} 页",
                project_id=meta.project_id,
                scene_narrations=scenes,
                progress=progress,
            )
        else:
            result = agent.run(
                message="按调用方讲稿生成视频",
                project_id=meta.project_id,
                narrations=written,
                progress=progress,
            )
    except Exception as exc:  # noqa: BLE001 - 失败要落进状态文件，不能只抛给终端
        write_status(
            work,
            state="failed",
            error=f"{type(exc).__name__}: {exc}"[:500],
            updated=time.time(),
            elapsed=round(time.time() - started, 1),
        )
        traceback.print_exc()
        return 1

    video = ""
    # 工程里存的是相对工程目录的路径（"out/final.mp4"），不是绝对路径——
    # 直接拿去 open 会「文件不存在」，而前面每一步都成功了，很难往这上面想。
    final = agent.store.resolve(meta.project_id, result.output_path)
    if final and final.exists():
        # 成片复制到工作目录：工程库里的路径带工程 id，不好交付，也不好找。
        shutil.copy2(final, work / VIDEO)
        video = str(work / VIDEO)
    write_status(
        work,
        state="succeeded",
        stage="完成",
        detail=result.summary,
        video=video,
        duration_s=result.duration,
        scene_count=result.scene_count,
        elapsed=round(time.time() - started, 1),
        updated=time.time(),
        error="",
    )
    print(
        f"\n完成：{result.scene_count} 个场景｜{result.duration:.1f} 秒｜"
        f"{time.time() - started:.0f} 秒渲完\n成片：{video or '（没有成片，看日志）'}"
    )
    if not video:
        return 1
    print(f"配音 {VOICE}｜逐场景讲稿与降级记录：python3 scripts/summary.py --dir {work}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
