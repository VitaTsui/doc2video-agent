"""Finding an external program, in the form a spawn can actually use.

This exists because of one Windows behaviour that has now cost this repo three
separate bugs. `npx`, `pnpm` and `uv` are installed as `.cmd` shims; `which`
finds them, because it consults PATHEXT — but `CreateProcess` will not run a
shim named without its extension. So code that checks with `which` and then
spawns the bare name passes its own check and fails at the spawn, with

    [WinError 2] 系统找不到指定的文件

for a program that is plainly installed. It looks like a missing dependency and
it is not one.

The fix each time was the same: use the answer the check already gave. Naming
it makes that the obvious thing to do rather than something to remember.
"""

from __future__ import annotations

import json
import os
import shutil
from pathlib import Path

# Where a user's own programs live when the process was not started from their
# shell. A window opened from the Dock inherits `/usr/bin:/bin:/usr/sbin:/sbin`
# and nothing else, so `claude` — installed at ~/.local/bin like every other
# per-user CLI — is invisible to `which`, the model layer degrades to the mock,
# and the app tells the user 「没写的会是占位文本」 for a CLI they have installed
# and are logged into. The shell's PATH is handed to the backend by the desktop
# shell; this is the net under it, and it costs a few `stat` calls on a miss.
_USER_BINS = (
    "~/.local/bin",
    "~/bin",
    "/usr/local/bin",
    "/opt/homebrew/bin",
    "~/.bun/bin",
    "~/.volta/bin",
    "~/.deno/bin",
    "~/.cargo/bin",
    "~/.npm-global/bin",
    "~/.yarn/bin",
)


def find(name: str) -> str | None:
    """The path to `name`, or None.

    Always the resolved path — never the name back — so a caller cannot use
    this to decide a program exists and then spawn something else.
    """
    if found := shutil.which(name):
        return found
    for directory in _USER_BINS:
        candidate = Path(directory).expanduser() / name
        if candidate.is_file() and os.access(candidate, os.X_OK):
            return str(candidate)
    return None


def require(name: str, hint: str) -> str:
    """The path to `name`, or a failure that says what to install.

    `hint` is shown to the person; it should name the thing to install rather
    than the symbol that is missing.
    """
    found = find(name)
    if found is None:
        raise FileNotFoundError(hint)
    return found


def node_command(node_dir: Path, package: str, bin_name: str = "") -> list[str] | None:
    """How to run a package's CLI out of `node_dir`, without npm.

    `npx` was the obvious way and it is not a safe one. The desktop runtime
    ships Node's binary and the workspace's `node_modules`, but not npm's own
    `lib/` — so the `npx` shim it also ships is a three-line file that requires
    `../lib/cli.js` and dies with MODULE_NOT_FOUND before it has resolved
    anything. It is present, it is executable, `which` finds it, and every check
    in this repo that asked 「有没有 npx」 answered yes. Measured in the installed
    app: the bridge died in 0.118s and every batch of the document understanding
    silently fell back to heuristics.

    A package's own entry point is right there in its `package.json`, and the
    interpreter is the one we shipped. Nothing in between to be missing.
    """
    package_json = node_dir / "node_modules" / package / "package.json"
    if not package_json.is_file():
        return None
    try:
        declared = json.loads(package_json.read_text(encoding="utf-8")).get("bin")
    except (OSError, json.JSONDecodeError):
        return None

    if isinstance(declared, str):
        entry = declared
    elif isinstance(declared, dict):
        entry = declared.get(bin_name or package) or next(iter(declared.values()), "")
    else:
        entry = ""
    if not entry:
        return None

    script = (package_json.parent / entry).resolve()
    if not script.is_file():
        return None

    node = node_dir / "bin" / ("node.exe" if os.name == "nt" else "node")
    if not node.is_file():
        node = node_dir / ("node.exe" if os.name == "nt" else "node")
    resolved = str(node) if node.is_file() else find("node")
    return [resolved, str(script)] if resolved else None
