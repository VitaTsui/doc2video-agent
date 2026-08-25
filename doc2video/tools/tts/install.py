"""Putting a voice pack somewhere the next update will not take it away.

Shared by the command line and by the window, which both offer the same thing
and must not drift on how it is done — the first version of this lived only in
the CLI, and the window would have grown a second copy with its own bugs.

**Not into the runtime.** That was the first version and it did not survive:
the engines went into the interpreter that was running, which for the desktop
app is the downloaded runtime, and updating the app replaces that whole
directory. Installing a voice and then updating meant installing it again, with
nothing saying why — reported as 「更新客户端后，为什么会需要重新下载语音包」.

The voices themselves were never the problem: `.onnx` models live beside the
projects and are untouched. It is the engine that reads them that was being
thrown away.

So they go to a directory of their own, outside the runtime, and the backend is
told to look there. `D2V_PACKAGES_DIR` is how the shell says where; without it
this falls back to installing in place, which is right for a source checkout
where there is no runtime to replace.
"""

from __future__ import annotations

import os
from pathlib import Path


def packages_dir() -> Path | None:
    """Where installed engines are kept, or None to install in place."""
    named = os.environ.get("D2V_PACKAGES_DIR", "").strip()
    return Path(named) if named else None


def install_into_runtime(packages: list[str]) -> str | None:
    """Put `packages` where this interpreter can find them. None on success.

    Three ways, because the interpreter this ends up in may have none of them.
    A uv-made environment ships no `pip` at all — and the packaged runtime is
    built with `uv pip install --target`, so it does not have one either. The
    first version of this command called `python -m pip` and failed on the
    developer's own checkout, which is where it was going to fail for everyone.
    """
    import subprocess
    import sys

    from ...core import programs

    into = packages_dir()
    target = ["--target", str(into)] if into is not None else []
    if into is not None:
        into.mkdir(parents=True, exist_ok=True)

    attempts: list[list[str]] = [[sys.executable, "-m", "pip", "install", *target, *packages]]
    if (uv := programs.find("uv")) is not None:
        attempts.append(
            [uv, "pip", "install", "--python", sys.executable, *target, *packages]
        )
    # Last resort: put pip there, then use it.
    attempts.append([sys.executable, "-m", "ensurepip", "--upgrade"])

    errors: list[str] = []
    for index, command in enumerate(attempts):
        print("$ " + " ".join(command))
        result = subprocess.run(command, capture_output=True, text=True, check=False)
        if result.returncode == 0:
            if command[-1] == "--upgrade":  # ensurepip: now retry the install
                return install_into_runtime(packages)
            return None
        errors.append(f"[{index + 1}] {(result.stderr or result.stdout).strip()[-300:]}")

    return "安装失败，试过的三种方式都不行：\n" + "\n".join(errors)
