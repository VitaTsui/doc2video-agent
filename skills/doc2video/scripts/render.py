#!/usr/bin/env python3
"""渲染成片。默认后台跑，因为它是分钟级的。

工具调用普遍只有一两分钟预算，而一支五分钟的片子渲十几分钟很正常——前台跑
必定被打断在中途，留下一个半成品和一个说不清发生了什么的超时。所以默认丢到
后台，用 job_status.py 轮询，和上一版的 make_video.py 一个道理。

渲染之前先跑一次 tsc。生成的场景是模型写的代码，一个类型错误在 Remotion 里
表现为那一场是空白的——片子渲成功了，中间黑了八秒。宁可停在这里报错。
"""

from __future__ import annotations

import argparse
import os
import subprocess
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from lib.workspace import (  # noqa: E402
    LOG,
    VIDEO,
    bootstrap,
    load_voicemap,
    node_bin,
    project_dir,
    write_status,
)


def main() -> int:
    parser = argparse.ArgumentParser(description="渲染成片")
    parser.add_argument("--out", required=True, type=Path, help="工作目录")
    parser.add_argument("--foreground", action="store_true", help="前台跑（会被工具预算打断）")
    parser.add_argument("--concurrency", type=int, default=0, help="并行度，0 交给 Remotion 定")
    parser.add_argument("--skip-typecheck", action="store_true", help="跳过 tsc（不建议）")
    args = parser.parse_args()

    work = bootstrap(args.out)
    project = project_dir(work)
    if not (project / "node_modules").is_dir():
        raise SystemExit(f"{project} 里没有 node_modules——先跑 init_project.py 装依赖")

    voicemap = load_voicemap(work)
    registry = project / "src" / "compositions" / "generated-scenes.ts"
    if "generatedScenes: GeneratedSceneItem[] = []" in registry.read_text(encoding="utf-8"):
        raise SystemExit("注册表还是空的——先跑 register_scenes.py")

    if not args.skip_typecheck:
        print("类型检查…", file=sys.stderr)
        check = subprocess.run(  # noqa: S603
            [node_bin("npx"), "tsc", "--noEmit"], cwd=project,
            capture_output=True, text=True
        )
        if check.returncode != 0:
            raise SystemExit(
                "生成的场景有类型错误，渲下去那几场会是空白画面：\n"
                + (check.stdout or check.stderr)[-2000:]
            )

    target = work / VIDEO
    command = [node_bin("npx"), "remotion", "render", "src/index.ts", "Main", str(target)]
    if args.concurrency > 0:
        command += ["--concurrency", str(args.concurrency)]

    started = time.time()
    # `write_status` 是合并写入，所以开跑时要把上一轮的结果字段一并清掉。不清
    # 的话 job_status 会读到上次那次渲染的 elapsed，报出「已用 1138 秒」——这一
    # 次才刚开始三秒。
    write_status(
        work, state="running", updated=started, started=started,
        elapsed=0, video="", error="",
    )

    if args.foreground:
        result = subprocess.run(command, cwd=project)  # noqa: S603
        return _finish(work, target, started, result.returncode, voicemap)

    # 后台跑的是**这个脚本自己**，不是 remotion。直接 Popen remotion 的话没有
    # 人在它结束时写状态文件，job_status.py 会一直报 running——渲完了也一样。
    log = work / LOG
    with log.open("w", encoding="utf-8") as sink:
        process = subprocess.Popen(  # noqa: S603
            [sys.executable, str(Path(__file__).resolve()), "--out", str(work),
             "--foreground", *(["--skip-typecheck"] if args.skip_typecheck else []),
             *(["--concurrency", str(args.concurrency)] if args.concurrency > 0 else [])],
            stdout=sink, stderr=subprocess.STDOUT,
            start_new_session=os.name != "nt",
        )
    print(
        f"渲染已在后台跑（pid {process.pid}）\n"
        f"  轮询：python3 scripts/job_status.py --out {work}\n"
        f"  日志：{log}\n"
        f"  预计：{voicemap['total'] / 60:.1f} 分钟的片子，通常渲几分钟到十几分钟"
    )
    return 0


def _finish(work: Path, target: Path, started: float, code: int, voicemap: dict) -> int:
    elapsed = round(time.time() - started, 1)
    if code != 0 or not target.exists():
        write_status(work, state="failed", updated=time.time(), elapsed=elapsed,
                     error=f"remotion render 退出码 {code}")
        return 1
    # 状态值是 job_status.py 的退出码契约的一半：succeeded=0 / failed=1 /
    # running=2。写成别的（比如 "done"）它会落到 unknown 分支，报「失败」。
    write_status(
        work, state="succeeded", updated=time.time(), elapsed=elapsed,
        video=str(target), error="",
        duration_s=voicemap["total"], scene_count=len(voicemap["scenes"]),
    )
    size = target.stat().st_size / 1024 / 1024
    print(f"\n成片 {target}（{size:.1f} MB，{elapsed:.0f} 秒）")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
