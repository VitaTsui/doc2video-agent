"""Command line interface.

Same agent, same project format as the HTTP API — useful for local runs, CI and
debugging a pipeline stage without a browser.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from .agent import Doc2VideoAgent
from .api.security import check_exposure
from .core.config import dependency_report, filter_report, get_settings
from .core.errors import Doc2VideoError
from .core.flags import report as flag_report
from .core.logging import setup_logging, use_utf8
from .storage.run_log import RunLog, summarize
from .tools.llm import llm_status
from .tools.renderer import renderer_status
from .tools.tts import TTSTool, voices_available


def main(argv: list[str] | None = None) -> int:
    use_utf8()
    parser = argparse.ArgumentParser(prog="doc2video", description="PDF / PPT 智能讲解视频 Agent")
    parser.add_argument("--log-level", default=None, help="DEBUG / INFO / WARNING")
    sub = parser.add_subparsers(dest="command", required=True)

    doctor = sub.add_parser("doctor", help="检查运行环境与可用能力")
    doctor.add_argument(
        "--strict",
        action="store_true",
        help="缺少关键能力时退出码非零（给 CI 的运行时自检用）",
    )
    doctor.set_defaults(func=cmd_doctor)

    run = sub.add_parser("run", help="从文档生成视频")
    run.add_argument("file", type=Path, help="PDF / PPT / PPTX 文件路径")
    run.add_argument("message", help="一句话说明你想要的视频")
    run.set_defaults(func=cmd_run)

    edit = sub.add_parser("edit", help="用一句话修改已有工程")
    edit.add_argument("project_id")
    edit.add_argument("message")
    edit.set_defaults(func=cmd_edit)

    show = sub.add_parser("show", help="查看工程概览")
    show.add_argument("project_id")
    show.set_defaults(func=cmd_show)

    projects = sub.add_parser("projects", help="列出全部工程")
    projects.set_defaults(func=cmd_projects)

    metrics = sub.add_parser("metrics", help="查看跨运行的耗时、成本、质量与放量对照")
    metrics.add_argument("--limit", type=int, default=500, help="参与统计的最近运行条数")
    metrics.set_defaults(func=cmd_metrics)

    upgrade = sub.add_parser("voice-upgrade", help="装一个更好的声音")
    upgrade.add_argument(
        "pack",
        nargs="?",
        default="edge",
        choices=["edge", "kokoro"],
        help="edge=播音腔（约 1MB，需联网）｜kokoro=本地神经语音（约 400MB）",
    )
    upgrade.add_argument("--yes", action="store_true", help="不询问直接安装")
    upgrade.set_defaults(func=cmd_voice_upgrade)

    voices = sub.add_parser("voices", help="下载 Piper 语音模型（Windows / Linux 的声音来源）")
    voices.add_argument(
        "name", nargs="?", default="", help="音色名，默认 zh_CN-huayan-medium（约 61MB）"
    )
    voices.set_defaults(func=cmd_voices)

    serve = sub.add_parser("serve", help="启动 API 服务")
    serve.add_argument("--host", default=None)
    serve.add_argument("--port", type=int, default=None)
    serve.add_argument("--reload", action="store_true")
    serve.set_defaults(func=cmd_serve)

    schemas = sub.add_parser("export-schemas", help="导出 JSON Schema 到 doc2video/schemas/json")
    schemas.set_defaults(func=cmd_export_schemas)

    args = parser.parse_args(argv)
    settings = get_settings()
    setup_logging(args.log_level or settings.log_level)

    try:
        return args.func(args)
    except Doc2VideoError as exc:
        print(f"错误[{exc.code}]：{exc.message}", file=sys.stderr)
        if exc.detail:
            print(json.dumps(exc.detail, ensure_ascii=False, indent=2), file=sys.stderr)
        return 1


# --------------------------------------------------------------------------
# commands
# --------------------------------------------------------------------------


def cmd_voices(args) -> int:
    """Fetch a voice model. A separate command on purpose.

    Downloading 61MB in the middle of a render looks exactly like a hang, so
    the provider reports itself unavailable and points here instead of fetching
    on demand.
    """
    from .tools.tts.piper import DEFAULT_VOICE, PiperProvider, download_voice

    settings = get_settings()
    directory = settings.storage_dir / "voices"
    name = args.name.strip() or DEFAULT_VOICE

    path = download_voice(name, directory)
    print(f"已下载：{path}")

    provider = PiperProvider(settings)
    if provider.available():
        print("Piper 现在可用")
    else:
        print(f"仍不可用：{provider.unavailable_reason()}")
    return 0


def cmd_voice_upgrade(args) -> int:
    """Install the better voice into whichever interpreter is running this.

    The engine adapter has been there since the measurement that justified it;
    what was missing is the one step a person cannot reasonably do themselves
    inside a packaged app — putting the package into the runtime that app
    downloaded. That is all this does.

    Its own command, and not something a render triggers: this fetches several
    hundred megabytes, and a download that starts in the middle of making a
    video is indistinguishable from a hang.
    """
    import subprocess
    import sys

    from .tools.tts.edge import EdgeProvider
    from .tools.tts.kokoro import KokoroProvider

    packs = {
        "edge": (
            EdgeProvider,
            ["edge-tts"],
            "微软的播音音色 zh-CN-YunyangNeural——官方标签 Professional / Reliable，"
            "是这批中文音色里唯一那个性格。",
            "约 1MB。合成走微软的在线端点，**要联网**；断了会自动改用本机的声音。",
            "edge",
        ),
        "kokoro": (
            KokoroProvider,
            ["kokoro", "misaki[zh]"],
            "一个 82M 参数的本地中文语音模型。系统自带的声音每次停顿几乎一样长"
            "（离散度 0.13），它是 0.66，会连着讲四秒再换气。",
            f"连同 torch 约 400MB，装到 {sys.prefix}。全程本地，不联网。",
            "auto（装上后自动排在 say 之后、Piper 之前）",
        ),
    }
    provider_cls, packages, why, cost, how = packs[args.pack]

    provider = provider_cls()
    if provider.available():
        print("已经装好了。可用音色：" + "、".join(provider.voices()))
        print(f"启用方式：D2V_TTS_PROVIDER={how}")
        return 0

    print(f"要装的是：{why}")
    print(f"代价：{cost}")
    if not args.yes:
        answer = input("现在装吗？[y/N] ").strip().lower()
        if answer not in ("y", "yes"):
            print("没有安装。想好了再跑一次这个命令。")
            return 0

    if (failure := _install_into_runtime(packages)) is not None:
        print(failure, file=sys.stderr)
        return 1

    # Import in a fresh interpreter: this one imported `kokoro` already and
    # cached the failure, so asking it again would say no whatever happened.
    module = "edge_tts" if args.pack == "edge" else "kokoro"
    check = subprocess.run(
        [sys.executable, "-c", f"import {module}; print('ok')"], capture_output=True, text=True
    )
    if check.returncode != 0:
        print(f"装完了但导入不了：{check.stderr.strip()[:200]}", file=sys.stderr)
        return 1
    print(f"装好了。启用方式：D2V_TTS_PROVIDER={how}")
    return 0


def _install_into_runtime(packages: list[str]) -> str | None:
    """Put `packages` into the interpreter running this. None on success.

    Three ways, because the interpreter this ends up in may have none of them.
    A uv-made environment ships no `pip` at all — and the packaged runtime is
    built with `uv pip install --target`, so it does not have one either. The
    first version of this command called `python -m pip` and failed on the
    developer's own checkout, which is where it was going to fail for everyone.
    """
    import subprocess
    import sys

    from .core import programs

    attempts: list[list[str]] = [[sys.executable, "-m", "pip", "install", *packages]]
    if (uv := programs.find("uv")) is not None:
        attempts.append([uv, "pip", "install", "--python", sys.executable, *packages])
    # Last resort: put pip there, then use it.
    attempts.append([sys.executable, "-m", "ensurepip", "--upgrade"])

    errors: list[str] = []
    for index, command in enumerate(attempts):
        print("$ " + " ".join(command))
        result = subprocess.run(command, capture_output=True, text=True, check=False)
        if result.returncode == 0:
            if command[-1] == "--upgrade":  # ensurepip: now retry the install
                return _install_into_runtime(packages)
            return None
        errors.append(f"[{index + 1}] {(result.stderr or result.stdout).strip()[-300:]}")

    return "安装失败，试过的三种方式都不行：\n" + "\n".join(errors)


def cmd_doctor(args) -> int:
    settings = get_settings()

    print("== 能力体检 ==")
    llm = llm_status(settings)
    if llm["available"]:
        print(f"模型       : {llm['provider']}｜{llm['model']}")
    else:
        configured = llm["configured"]
        note = (
            "未配置（讲稿由调用方提供）"
            if configured in ("", "mock")
            else f"{configured} 不可用"
        )
        print(f"模型       : {note}")
    tts = TTSTool(settings)
    voices = voices_available(settings)
    print(f"TTS        : {tts.provider_name}")
    # Worth printing because the number differs by platform in a way that
    # changes what a user can ask for: a dozen on macOS, the one model the
    # runtime ships on Windows and Linux, none at all under silence.
    print(f"可用嗓音   : {('、'.join(voices)) if voices else '（无）'}")

    print("\n渲染器：")
    for name, info in renderer_status(settings).items():
        mark = "✓" if info["available"] else "✗"
        reason = "" if info["available"] else f"  ({info['reason']})"
        print(f"  {mark} {name}{reason}")

    print("\n外部依赖：")
    # Where a binary came from matters: a vendored copy travels with the install,
    # a system one does not.
    source_label = {"bundled": "内置", "system": "系统", "configured": "指定", "missing": "缺失"}
    for name, info in dependency_report().items():
        mark = "✓" if info["available"] else "✗"
        source = source_label.get(str(info.get("source")), "")
        print(f"  {mark} {name:8s} [{source}] {info['purpose']}")

    print("\n滤镜：")
    for name, info in filter_report().items():
        mark = "✓" if info["available"] else "✗"
        print(f"  {mark} {name:8s} {info['purpose']}")

    print(f"\n工程目录：{settings.projects_dir.resolve()}")

    # A report that only ever prints is a report nobody acts on. The packaged
    # Windows runtime shipped mute for several releases while this very check
    # ran in CI and wrote 「TTS: silent」 in the log — the voice was in the
    # archive, nothing was looking where it landed, and printing it was not
    # enough to stop the release.
    if getattr(args, "strict", False):
        missing = [name for name, ok in _essentials(settings).items() if not ok]
        if missing:
            print(f"\n自检失败：{'、'.join(missing)}", file=sys.stderr)
            return 1
    return 0


def _essentials(settings) -> dict[str, bool]:
    """What a runtime has to be able to do to be worth shipping."""
    return {
        # Silence is a legitimate fallback at run time and a defect in a build:
        # the packaged runtime carries a voice, so resolving to `silent` means
        # it is not being found.
        "语音（TTS 落到了 silent）": TTSTool(settings).provider_name != "silent",
        "渲染器（一个可用的都没有）": any(
            info["available"] for info in renderer_status(settings).values()
        ),
    }


def cmd_run(args) -> int:
    agent = Doc2VideoAgent()
    result = agent.run(message=args.message, files=[args.file], progress=_print_progress)
    _print_result(result)
    return 0


def cmd_edit(args) -> int:
    agent = Doc2VideoAgent()
    result = agent.run(
        message=args.message, project_id=args.project_id, progress=_print_progress
    )
    _print_result(result)
    return 0


def cmd_show(args) -> int:
    project = Doc2VideoAgent().get_project(args.project_id)
    print(f"工程 {project.project_id}｜状态 {project.status}｜来源 {project.source.file}")
    print(f"标题：{project.document.title}｜主题：{project.document.topic}")
    print(f"时长：{project.total_duration():.1f} 秒（目标 {project.intent.duration} 秒）")
    print(f"成片：{project.render.output_path or '（未生成）'}")
    if project.quality:
        dimensions = "｜".join(f"{d.name} {d.score:.0f}" for d in project.quality.dimensions)
        print(f"质量：{project.quality.score:.1f} 分（{dimensions}）")
    if project.telemetry:
        print(
            f"上次运行：{project.telemetry.duration_s:.0f} 秒｜"
            f"降级 {len(project.telemetry.degradations)} 处"
        )
    print("\n场景：")
    for scene in project.scenes:
        actions = "、".join(f"{a.type}@{a.at:.1f}s" for a in scene.actions if a.target) or "无"
        print(
            f"  {scene.scene_id}  p{scene.source_page:<3} "
            f"{scene.duration:6.1f}s  动作：{actions}"
        )
        print(f"      {scene.narration[:60]}")
    if project.review:
        print("\n质检：")
        for finding in project.review:
            print(f"  [{finding.severity}] {finding.scene_id or '-'} {finding.message}")
    return 0


def cmd_projects(_args) -> int:
    items = Doc2VideoAgent().list_projects()
    if not items:
        print("暂无工程")
        return 0
    for item in items:
        print(
            f"{item['project_id']}  {item['status']:<10} {item['duration']:6.1f}s  "
            f"{item['source']}  {item['title'][:30]}"
        )
    return 0


def cmd_metrics(args) -> int:
    settings = get_settings()
    records = RunLog(settings).recent(args.limit)
    summary = summarize(records)

    if not summary.get("runs"):
        print("还没有运行记录。跑一次 `doc2video run` 之后再看。")
        return 0

    duration, quality = summary["duration_s"], summary["quality"]

    print(f"== 运行统计（最近 {summary['runs']} 次）==")
    print(f"成功／失败：{summary['succeeded']} / {summary['failed']}")
    if duration["count"]:
        print(f"总耗时　　：中位数 {duration['median']}s｜p95 {duration['p95']}s")
    # Quality shows the low tail, not p95 — the bad end of a score is the low one.
    if quality["count"]:
        print(f"质量分　　：中位数 {quality['median']}｜最差 5% {quality['p5']}")

    print("\n各阶段耗时（中位数 / p95 秒）：")
    for name, stats in summary["stages"].items():
        failed = f"｜失败 {stats['failed']}" if stats["failed"] else ""
        print(f"  {name:12s} {stats['median']:>8} / {stats['p95']}{failed}")

    if summary["degradations"]:
        print("\n降级次数（跑完了，但质量打折）：")
        for what, count in summary["degradations"].items():
            print(f"  {count:>4} × {what}")

    print("\n放量对照：")
    # Historical records keep the flag names they were written with, so a flag
    # that was renamed or retired still shows up here — report it as retired
    # rather than crashing on a name the current build no longer knows.
    current = flag_report(settings)
    for name, arms in summary["flags"].items():
        percent = current.get(name, {}).get("percent")
        share = f"当前放量 {percent}%" if percent is not None else "已下线"
        print(f"  {name}（{share}）")
        for arm, stats in sorted(arms.items()):
            quality = stats["quality_median"]
            print(
                f"    {arm:3s} 运行 {stats['runs']:>3}｜质量 "
                f"{quality if quality is not None else '—'}"
                f"｜耗时 {stats['duration_s_median']}s"
            )
    return 0


def cmd_serve(args) -> int:
    import uvicorn

    settings = get_settings()
    host = args.host or settings.host
    # Refuse to come up wide open. Every route spends quota or serves other
    # people's projects, so a public bind without a token is not a warning.
    check_exposure(host, settings.api_token)
    if settings.mcp_enabled:
        print(f"MCP (Streamable HTTP)：http://{host}:{args.port or settings.port}/mcp")
    uvicorn.run(
        "doc2video.api.app:app",
        host=host,
        port=args.port or settings.port,
        reload=args.reload,
    )
    return 0


def cmd_export_schemas(_args) -> int:
    from .schemas import DocumentModel, Scene, Timeline, VideoProject

    out_dir = Path(__file__).resolve().parent / "schemas" / "json"
    out_dir.mkdir(parents=True, exist_ok=True)
    exports = {
        "document.schema.json": DocumentModel,
        "scene.schema.json": Scene,
        "timeline.schema.json": Timeline,
        "project.schema.json": VideoProject,
    }
    for filename, model in exports.items():
        (out_dir / filename).write_text(
            json.dumps(model.model_json_schema(), ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        print(f"写入 {out_dir / filename}")
    return 0


# --------------------------------------------------------------------------
# helpers
# --------------------------------------------------------------------------


def _print_progress(stage: str, message: str, done: int = 0, total: int = 0) -> None:
    """One line per step, on stderr so stdout stays pipeable.

    ``done``/``total`` only exist for the stages that loop over scenes; showing
    "0/0" for the rest would be noise.
    """
    counter = f" {done}/{total}" if total else ""
    print(f"  · [{stage}]{counter} {message}", file=sys.stderr)


def _print_result(result) -> None:
    print(f"\n工程：{result.project_id}｜状态：{result.status}")
    print(f"说明：{result.summary}")
    print(f"场景：{result.scene_count} 个｜总时长：{result.duration:.1f} 秒")
    print(f"成片：{result.output_path or '（未生成）'}")
    if result.review:
        print("质检：")
        for finding in result.review:
            scene = finding.get("scene_id") or "-"
            print(f"  [{finding['severity']}] {scene} {finding['message']}")


if __name__ == "__main__":
    raise SystemExit(main())
