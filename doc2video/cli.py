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
from .core.config import dependency_report, filter_report, get_settings
from .core.errors import Doc2VideoError
from .core.flags import report as flag_report
from .core.logging import setup_logging
from .storage.run_log import RunLog, summarize
from .tools.llm import get_llm
from .tools.renderer import renderer_status
from .tools.tts import TTSTool

LLM_SOURCE_LABEL = {
    "anthropic_api": "API Key",
    "claude_code": "Claude Code CLI",
    "mock": "—",
}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="doc2video", description="PDF / PPT 智能讲解视频 Agent")
    parser.add_argument("--log-level", default=None, help="DEBUG / INFO / WARNING")
    sub = parser.add_subparsers(dest="command", required=True)

    doctor = sub.add_parser("doctor", help="检查运行环境与可用能力")
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


def cmd_doctor(_args) -> int:
    settings = get_settings()
    llm = get_llm(settings)

    print("== 能力体检 ==")
    availability = "可用" if llm.available else "不可用（将使用启发式降级）"
    source = LLM_SOURCE_LABEL.get(llm.source, llm.source)
    print(f"LLM        : {availability}｜模型 {llm.model}｜来源 {source}")
    print(f"TTS        : {TTSTool(settings).provider_name}")

    print("\n渲染器：")
    for name, info in renderer_status().items():
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
    return 0


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
        cost = project.telemetry.cost_usd()
        print(
            f"上次运行：{project.telemetry.duration_s:.0f} 秒｜"
            f"{f'${cost:.4f}' if cost is not None else '成本未知'}｜"
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
    cost = summary["cost_usd"]
    median_cost = cost["per_run_median"]
    priced = f"（{cost['priced_runs']} 次可定价）" if cost["priced_runs"] else "（无可定价记录）"

    print(f"== 运行统计（最近 {summary['runs']} 次）==")
    print(f"成功／失败：{summary['succeeded']} / {summary['failed']}")
    if duration["count"]:
        print(f"总耗时　　：中位数 {duration['median']}s｜p95 {duration['p95']}s")
    # Quality shows the low tail, not p95 — the bad end of a score is the low one.
    if quality["count"]:
        print(f"质量分　　：中位数 {quality['median']}｜最差 5% {quality['p5']}")
    print(
        f"成本　　　：合计 ${cost['total']:.4f}｜单次中位数 "
        f"{f'${median_cost:.4f}' if median_cost is not None else '—'}{priced}"
    )
    print(f"token　　 ：{summary['tokens']:,}")

    print("\n各阶段耗时（中位数 / p95 秒）：")
    for name, stats in summary["stages"].items():
        failed = f"｜失败 {stats['failed']}" if stats["failed"] else ""
        print(f"  {name:12s} {stats['median']:>8} / {stats['p95']}{failed}")

    if summary["degradations"]:
        print("\n降级次数（跑完了，但质量打折）：")
        for what, count in summary["degradations"].items():
            print(f"  {count:>4} × {what}")

    if summary["providers"]:
        calls = "｜".join(f"{name} {count}" for name, count in summary["providers"].items())
        print(f"\n实际应答的 provider：{calls}")

    print("\n放量对照（记录的是分支，不是实际 provider）：")
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
            cost_median = stats["cost_usd_median"]
            print(
                f"    {arm:3s} 运行 {stats['runs']:>3}｜质量 "
                f"{quality if quality is not None else '—'}｜成本 "
                f"{f'${cost_median:.4f}' if cost_median is not None else '—'}"
                f"｜耗时 {stats['duration_s_median']}s"
            )
    return 0


def cmd_serve(args) -> int:
    import uvicorn

    settings = get_settings()
    uvicorn.run(
        "doc2video.api.app:app",
        host=args.host or settings.host,
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


def _print_progress(stage: str, message: str) -> None:
    print(f"  · [{stage}] {message}", file=sys.stderr)


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
