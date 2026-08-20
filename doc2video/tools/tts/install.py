"""Putting a voice pack into the interpreter that is running.

Shared by the command line and by the window, which both offer the same thing
and must not drift on how it is done — the first version of this lived only in
the CLI, and the window would have grown a second copy with its own bugs.
"""

from __future__ import annotations


def install_into_runtime(packages: list[str]) -> str | None:
    """Put `packages` into the interpreter running this. None on success.

    Three ways, because the interpreter this ends up in may have none of them.
    A uv-made environment ships no `pip` at all — and the packaged runtime is
    built with `uv pip install --target`, so it does not have one either. The
    first version of this command called `python -m pip` and failed on the
    developer's own checkout, which is where it was going to fail for everyone.
    """
    import subprocess
    import sys

    from ...core import programs

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
                return install_into_runtime(packages)
            return None
        errors.append(f"[{index + 1}] {(result.stderr or result.stdout).strip()[-300:]}")

    return "安装失败，试过的三种方式都不行：\n" + "\n".join(errors)
