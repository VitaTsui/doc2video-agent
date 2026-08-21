"""Finding a program the user installed for themselves.

A window opened from the Dock does not inherit the shell's PATH — macOS gives
it launchd's `/usr/bin:/bin:/usr/sbin:/sbin` — so `which` cannot see `claude`,
`npx` or anything else living under the home directory. The desktop shell hands
the backend the shell's PATH; this is the net under that, and the reason the
app once offered to write placeholder text for a CLI that was installed and
logged in.
"""

from __future__ import annotations

import os
import stat

from doc2video.core import programs


def _executable(path):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("#!/bin/sh\nexit 0\n")
    path.chmod(path.stat().st_mode | stat.S_IXUSR)
    return path


def test_a_cli_under_the_home_directory_is_found_without_it_on_path(tmp_path, monkeypatch):
    home = tmp_path / "home"
    tool = _executable(home / ".local" / "bin" / "made-up-tool")
    monkeypatch.setenv("PATH", "/usr/bin:/bin")
    monkeypatch.setattr(programs, "_USER_BINS", ("~/.local/bin",))
    monkeypatch.setenv("HOME", str(home))

    assert programs.find("made-up-tool") == str(tool)


def test_what_is_not_installed_is_still_not_installed(tmp_path, monkeypatch):
    monkeypatch.setenv("PATH", str(tmp_path))
    monkeypatch.setattr(programs, "_USER_BINS", (str(tmp_path / "nowhere"),))

    assert programs.find("made-up-tool") is None


def test_a_directory_named_like_the_tool_is_not_the_tool(tmp_path, monkeypatch):
    """`is_file` matters: ~/.local/bin/claude could be a directory."""
    (tmp_path / "made-up-tool").mkdir()
    monkeypatch.setenv("PATH", "/usr/bin:/bin")
    monkeypatch.setattr(programs, "_USER_BINS", (str(tmp_path),))

    assert programs.find("made-up-tool") is None


def test_something_unreadable_is_not_offered_as_a_command(tmp_path, monkeypatch):
    """A path that cannot be executed would fail at the spawn, not here."""
    path = tmp_path / "made-up-tool"
    path.write_text("#!/bin/sh\n")
    path.chmod(path.stat().st_mode & ~stat.S_IXUSR & ~stat.S_IXGRP & ~stat.S_IXOTH)
    monkeypatch.setenv("PATH", "/usr/bin:/bin")
    monkeypatch.setattr(programs, "_USER_BINS", (str(tmp_path),))

    assert programs.find("made-up-tool") is None or os.geteuid() == 0


def test_a_package_cli_runs_without_npm(tmp_path, monkeypatch):
    """`npx` is not a dependency we get to assume.

    The desktop runtime ships Node and the workspace's node_modules but not
    npm's own `lib/`, so the `npx` it also ships is a shim that dies with
    MODULE_NOT_FOUND. It is present, executable, and found by `which` — which
    is why every 「有没有 npx」 check in this repo answered yes while nothing
    could start. Measured in the installed app: the model bridge died in 0.118s
    and all five batches of the document understanding fell back to heuristics.
    """
    import json

    node_dir = tmp_path / "node"
    node = _executable(node_dir / "bin" / "node")
    package = node_dir / "node_modules" / "some-cli"
    package.mkdir(parents=True)
    (package / "package.json").write_text(json.dumps({"bin": {"some-cli": "./dist/cli.js"}}))
    (package / "dist").mkdir()
    (package / "dist" / "cli.js").write_text("// entry\n")

    assert programs.node_command(node_dir, "some-cli") == [
        str(node),
        str(package / "dist" / "cli.js"),
    ]


def test_a_package_that_is_not_installed_has_no_command(tmp_path):
    assert programs.node_command(tmp_path, "some-cli") is None


def test_a_bin_pointing_at_nothing_is_not_a_command(tmp_path):
    """A pruned install leaves the manifest and not the file."""
    import json

    node_dir = tmp_path / "node"
    _executable(node_dir / "bin" / "node")
    package = node_dir / "node_modules" / "some-cli"
    package.mkdir(parents=True)
    (package / "package.json").write_text(json.dumps({"bin": "./dist/gone.js"}))

    assert programs.node_command(node_dir, "some-cli") is None
