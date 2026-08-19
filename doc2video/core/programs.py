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

import shutil


def find(name: str) -> str | None:
    """The path to `name`, or None.

    Always the resolved path — never the name back — so a caller cannot use
    this to decide a program exists and then spawn something else.
    """
    return shutil.which(name)


def require(name: str, hint: str) -> str:
    """The path to `name`, or a failure that says what to install.

    `hint` is shown to the person; it should name the thing to install rather
    than the symbol that is missing.
    """
    found = shutil.which(name)
    if found is None:
        raise FileNotFoundError(hint)
    return found
