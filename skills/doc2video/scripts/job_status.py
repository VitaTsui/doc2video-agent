#!/usr/bin/env python3
"""渲染跑到哪儿了。

后台任务与这里之间只有一个接口：``状态.json``。它整份重写，所以读到的永远是
一份完整的状态，不会读到写了一半的行。

    python3 scripts/job_status.py --out <工作目录>
    python3 scripts/job_status.py --out <工作目录> --wait 90   # 最多等 90 秒再返回

退出码是给脚本判断用的：**0 成功、1 失败、2 还在跑**。别用输出去 grep 状态。

渲染是这条链路上唯一的分钟级步骤，也是唯一会用到这个脚本的。--wait 给的秒数
要留在工具预算之内——超时被打断的是这个轮询，不是渲染，渲染在自己的进程组里
继续跑，再调一次就好。
"""

from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from lib.workspace import LOG, bootstrap, read_status  # noqa: E402

STATE_CODE = {"succeeded": 0, "failed": 1, "running": 2}


def main() -> int:
    parser = argparse.ArgumentParser(description="看渲染跑到哪儿了")
    parser.add_argument("--out", required=True, type=Path, help="工作目录")
    parser.add_argument(
        "--wait", type=int, default=0, help="最多等多少秒（默认不等，看一眼就返回）"
    )
    parser.add_argument("--tail", type=int, default=6, help="附带日志最后几行")
    args = parser.parse_args()

    work = bootstrap(args.out)
    deadline = time.time() + max(args.wait, 0)
    while True:
        status = read_status(work)
        state = status.get("state", "")
        if state != "running" or time.time() >= deadline:
            break
        time.sleep(5)

    if not status:
        print("还没跑过渲染（没有 状态.json）")
        return 1

    state = status.get("state", "unknown")
    elapsed = status.get("elapsed") or (time.time() - status.get("started", time.time()))
    line = f"{state}｜{status.get('stage', '')} {status.get('detail', '')}".strip()
    if status.get("total"):
        line += f"｜{status.get('done', 0)}/{status['total']}"
    print(f"{line}｜已用 {elapsed:.0f} 秒")

    if state == "succeeded":
        print(f"成片 {status.get('video') or '（无）'}｜{status.get('duration_s', 0):.1f} 秒"
              f"｜{status.get('scene_count', 0)} 个场景")
    if state == "failed":
        print(f"失败：{status.get('error', '')}")
    if args.tail:
        log = work / LOG
        if log.exists():
            tail = log.read_text(encoding="utf-8", errors="ignore").splitlines()[-args.tail :]
            print("\n日志末尾：")
            for row in tail:
                print(f"  {row}")
    return STATE_CODE.get(state, 1)


if __name__ == "__main__":
    raise SystemExit(main())
