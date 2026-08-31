#!/usr/bin/env python3
"""把 Remotion 模板复制成这个片子自己的工程，并确认依赖装得上。

模板是**复制**过去的，不是链接。一支片子的场景是生成的代码，改模板不该回头
改动已经出片的工程；反过来，工程里改坏了也只坏它自己。

依赖装在工程目录里而不是模板里，理由同上：两支片子可以用不同版本的 Remotion，
而模板永远是干净的。
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from lib.workspace import (  # noqa: E402
    PROJECT,
    bootstrap,
    node_bin,
    project_dir,
    public_dir,
)

SKILL_ROOT = Path(__file__).resolve().parents[1]
TEMPLATE = SKILL_ROOT / "template"


def main() -> int:
    parser = argparse.ArgumentParser(description="初始化这支片子的 Remotion 工程")
    parser.add_argument("--out", required=True, type=Path, help="工作目录（prepare.py 用的那个）")
    parser.add_argument(
        "--skip-install",
        action="store_true",
        help="只复制模板，不装 node 依赖（离线环境里先复制、之后自己装）",
    )
    args = parser.parse_args()

    work = bootstrap(args.out)
    target = project_dir(work)

    if not TEMPLATE.is_dir():
        raise SystemExit(f"技能包里没有模板：{TEMPLATE}")

    # 认 package.json，不认目录。`make_voice.py` 会先把配音写进
    # 项目/public/audio/，于是这个目录可能已经存在却还没有模板——只看目录存不
    # 存在的话，这一步会说「已初始化」然后跳过，留下一个没有模板的空壳，而错
    # 误要到渲染时才以「找不到 src/index.ts」的样子冒出来。
    if (target / "package.json").exists():
        # 真初始化过就不要再来一遍：src/scenes/ 里可能已经有写好的场景，
        # 覆盖等于把画面全删了，而这一步看起来只是「重新初始化」。
        print(f"{target} 已经是一个工程，跳过复制（要重来就先删掉它）", file=sys.stderr)
    else:
        shutil.copytree(TEMPLATE, target, dirs_exist_ok=True)
        print(f"模板 → {target}")

    for sub in ("audio", "broll"):
        (public_dir(work) / sub).mkdir(parents=True, exist_ok=True)

    if args.skip_install:
        print("跳过依赖安装（--skip-install）")
        return _report(work, target, installed=False)

    npm = node_bin("npm")

    print("装 Remotion 依赖（分钟级）…", file=sys.stderr)
    result = subprocess.run(  # noqa: S603
        [npm, "install", "--no-audit", "--no-fund"],
        cwd=target,
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        tail = (result.stderr or result.stdout)[-1500:]
        raise SystemExit(f"npm install 失败：\n{tail}")

    return _report(work, target, installed=True)


def _report(work: Path, target: Path, *, installed: bool) -> int:
    scenes = target / "src" / "scenes"
    print(
        f"\n工程目录 {target}\n"
        f"  场景组件写到 {scenes}/（一场一个 .tsx）\n"
        f"  配音落在 {public_dir(work) / 'audio'}/\n"
        f"  B-roll 素材落在 {public_dir(work) / 'broll'}/\n"
        f"  依赖：{'已装' if installed else '未装，渲染前要 npm install'}\n"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
