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
