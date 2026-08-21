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
