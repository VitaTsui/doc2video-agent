"""Binary resolution: configured path > system PATH > vendored wheel."""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from doc2video.core.config import get_settings
from doc2video.tools import media_binaries


@pytest.fixture(autouse=True)
def _clear_caches():
    media_binaries.reset_cache()
    get_settings.cache_clear()
    yield
    media_binaries.reset_cache()
    get_settings.cache_clear()


def test_ffmpeg_resolves_from_somewhere():
    binary = media_binaries.ffmpeg()
    assert binary.source in {"configured", "system", "bundled", "missing"}
    if binary.available:
        assert Path(binary.path).exists()


def test_configured_path_wins(monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
    fake = tmp_path / "my-ffmpeg"
    fake.write_text("#!/bin/sh\n", encoding="utf-8")
    monkeypatch.setenv("D2V_FFMPEG_PATH", str(fake))
    media_binaries.reset_cache()
    get_settings.cache_clear()

    binary = media_binaries.ffmpeg()
    assert binary.source == "configured"
    assert binary.path == str(fake)


def test_missing_configured_path_falls_through(monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
    monkeypatch.setenv("D2V_FFMPEG_PATH", str(tmp_path / "does-not-exist"))
    media_binaries.reset_cache()
    get_settings.cache_clear()

    binary = media_binaries.ffmpeg()
    assert binary.source != "configured"


def test_bundled_binary_is_used_when_path_has_none(monkeypatch: pytest.MonkeyPatch):
    if media_binaries._vendored("ffmpeg") is None:
        pytest.skip("本机没有装任何内置 ffmpeg wheel")
    # Empty PATH: the only remaining source is the vendored wheel.
    monkeypatch.setenv("PATH", "")
    monkeypatch.delenv("D2V_FFMPEG_PATH", raising=False)
    media_binaries.reset_cache()
    get_settings.cache_clear()

    binary = media_binaries.ffmpeg()
    assert binary.source == "bundled"
    assert Path(binary.path).exists()


def test_ffprobe_can_be_vendored_as_well(monkeypatch: pytest.MonkeyPatch):
    """The reason for adding ffmpeg-binaries: imageio-ffmpeg has no ffprobe."""
    if media_binaries._from_ffmpeg_binaries("ffprobe") is None:
        pytest.skip("本机没有装 ffmpeg-binaries")
    monkeypatch.setenv("PATH", "")
    monkeypatch.delenv("D2V_FFPROBE_PATH", raising=False)
    media_binaries.reset_cache()
    get_settings.cache_clear()

    binary = media_binaries.ffprobe()
    assert binary.source == "bundled"
    assert Path(binary.path).exists()


def test_the_wheel_carrying_ffprobe_wins():
    """Both wheels can be installed; only one of them has ffprobe and drawtext."""
    preferred = media_binaries._from_ffmpeg_binaries("ffmpeg")
    if preferred is None or media_binaries._from_imageio("ffmpeg") is None:
        pytest.skip("需要同时装了两个 wheel 才能验证优先级")

    assert media_binaries._vendored("ffmpeg") == preferred


def test_console_script_shim_is_not_a_system_install(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
):
    """ffmpeg-binaries drops a Python launcher named `ffmpeg` beside python.

    Taking it for a system install would mislabel the source and put an
    interpreter start-up in front of every ffmpeg call a render makes.
    """
    scripts = tmp_path / "bin"
    scripts.mkdir()
    shim = scripts / "ffmpeg"
    shim.write_text("#!/usr/bin/env python\nfrom ffmpeg.executable import _run\n")
    shim.chmod(0o755)
    monkeypatch.setattr(media_binaries.sys, "executable", str(scripts / "python3"))
    monkeypatch.setenv("PATH", str(scripts))
    monkeypatch.delenv("D2V_FFMPEG_PATH", raising=False)
    media_binaries.reset_cache()
    get_settings.cache_clear()

    binary = media_binaries.ffmpeg()
    assert binary.path != str(shim)
    assert binary.source != "system"


def test_probe_duration_matches_wav_header(tmp_path: Path):
    import struct
    import wave

    path = tmp_path / "tone.wav"
    with wave.open(str(path), "wb") as handle:
        handle.setnchannels(1)
        handle.setsampwidth(2)
        handle.setframerate(22050)
        handle.writeframes(struct.pack("<h", 0) * 22050 * 2)  # exactly 2 seconds

    if not media_binaries.ffmpeg().available and not media_binaries.ffprobe().available:
        pytest.skip("本机没有可用的 ffmpeg / ffprobe")
    assert media_binaries.probe_duration(path) == pytest.approx(2.0, abs=0.05)


def test_filter_report_answers_for_this_build():
    """Having ffmpeg is not the same as having drawtext."""
    from doc2video.core.config import filter_report

    report = filter_report()
    assert set(report) == {"drawtext"}
    assert isinstance(report["drawtext"]["available"], bool)


def test_dependency_report_names_the_source():
    from doc2video.core.config import dependency_report

    report = dependency_report()
    ffmpeg_entry = report["ffmpeg"]
    assert ffmpeg_entry["source"] in {"configured", "system", "bundled", "missing"}
    assert "soffice" in report
    if ffmpeg_entry["available"]:
        assert os.path.basename(str(ffmpeg_entry["path"]))
