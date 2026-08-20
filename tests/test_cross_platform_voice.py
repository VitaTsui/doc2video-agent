"""Speech and subtitles on the platforms that had neither.

Two silent failures kept this project macOS-only, and both looked like success:
a run off macOS produced a correctly timed, correctly subtitled, *mute* video,
and a run on Windows dropped its burned-in subtitles with a warning nobody
reads. Neither raised anything.
"""

from __future__ import annotations

from pathlib import Path

from doc2video.core.config import Settings
from doc2video.tools.parsers.slide_raster import FONT_CANDIDATES, font_candidates
from doc2video.tools.tts.piper import PiperProvider
from doc2video.tools.tts.providers import AUTO_ORDER, resolve_provider


def test_every_platform_has_a_font_to_fall_back_on():
    """The list is read twice — by the rasteriser and by the subtitle burner —
    so a platform missing from it loses its subtitles without saying so."""
    joined = " ".join(FONT_CANDIDATES)
    assert "/System/Library/Fonts" in joined, "macOS"
    assert "C:/Windows/Fonts" in joined, "Windows"
    assert "/usr/share/fonts" in joined, "Linux"


def test_a_bundled_font_outranks_whatever_the_machine_has(tmp_path: Path, monkeypatch):
    """A packaged app cannot assume the machine has any CJK font at all."""
    from doc2video.core import config

    fonts = tmp_path / "fonts"
    fonts.mkdir()
    (fonts / "NotoSansSC.otf").write_bytes(b"not really a font")
    monkeypatch.setattr(
        config, "get_settings", lambda: Settings(node_dir=tmp_path / "node")
    )

    candidates = font_candidates()
    assert candidates[0].endswith("NotoSansSC.otf")
    assert len(candidates) == len(FONT_CANDIDATES) + 1


def test_piper_is_in_the_auto_order_after_say():
    """`say` is instant and needs no model; piper is what everyone else gets."""
    names = [cls.name for cls in AUTO_ORDER]
    assert names.index("macos_say") < names.index("piper") < names.index("silent")


def test_piper_says_what_it_needs_rather_than_downloading_mid_render(tmp_path: Path):
    """61MB fetched during a render is indistinguishable from a hang."""
    provider = PiperProvider(Settings(storage_dir=tmp_path))
    if provider.available():  # a machine that already has a voice installed
        return
    reason = provider.unavailable_reason()
    assert "doc2video voices" in reason or "piper-tts" in reason


def test_the_voice_shipped_with_the_runtime_is_found(tmp_path: Path):
    """The packaged app ships a voice; nothing was looking where it lands.

    The build downloads one into `<runtime>/voices` so the first render does
    not wait on 61MB, but the search only ever covered the data directory —
    which `doc2video voices` writes to and a fresh install has nothing in.
    Piper then called itself unavailable, and on a machine with no `say` the
    order fell through to silence: an audio track, correct durations, no sound.
    """
    runtime = tmp_path / "runtime"
    (runtime / "voices").mkdir(parents=True)
    (runtime / "voices" / "zh_CN-huayan-medium.onnx").write_bytes(b"not really a model")

    provider = PiperProvider(
        Settings(storage_dir=tmp_path / "data", node_dir=runtime / "node")
    )
    found = provider.voice_path()
    assert found is not None and found.name == "zh_CN-huayan-medium.onnx"


def test_a_downloaded_voice_wins_over_the_shipped_one(tmp_path: Path):
    """Someone who ran `doc2video voices` chose that one; the other is a default."""
    runtime = tmp_path / "runtime"
    (runtime / "voices").mkdir(parents=True)
    (runtime / "voices" / "zh_CN-huayan-medium.onnx").write_bytes(b"shipped")
    data = tmp_path / "data"
    (data / "voices").mkdir(parents=True)
    (data / "voices" / "zh_CN-huayan-medium.onnx").write_bytes(b"downloaded")

    provider = PiperProvider(Settings(storage_dir=data, node_dir=runtime / "node"))
    assert provider.voice_path() == data / "voices" / "zh_CN-huayan-medium.onnx"


def test_an_unavailable_provider_never_leaves_the_pipeline_without_one():
    """Voicing must degrade to silence, not fail: the video is still watchable."""
    assert resolve_provider("piper").available()
    assert resolve_provider("nonexistent").available()


def test_the_cli_progress_printer_matches_what_the_pipeline_sends(capsys):
    """The CLI is the one caller the test suite never routes through, which is
    how it kept a two-argument callback after the pipeline grew to four and
    `doc2video run` began crashing at its first step."""
    import inspect

    from doc2video.agent.executor import Executor
    from doc2video.cli import _print_progress

    Executor(None)  # type: ignore[arg-type]  # just to touch the default
    _print_progress("render", "渲染场景 scene_01", 2, 9)
    assert "2/9" in capsys.readouterr().err

    # Every emission point passes four; the printer must accept four.
    assert len(inspect.signature(_print_progress).parameters) == 4


def test_output_streams_are_made_utf8_before_anything_prints(monkeypatch):
    """Windows consoles default to a legacy code page, and every message this
    project writes is in Chinese — so `doctor` did not print mangled text on
    Windows, it crashed on its first line."""
    import io
    import sys

    from doc2video.core.logging import use_utf8

    class Legacy(io.StringIO):
        encoding = "cp1252"
        reconfigured: dict = {}

        def reconfigure(self, **kwargs):
            Legacy.reconfigured = kwargs

    monkeypatch.setattr(sys, "stdout", Legacy())
    use_utf8()
    assert Legacy.reconfigured.get("encoding") == "utf-8"
    # Never raise on a stream that cannot be reconfigured: losing the message
    # is bad, taking the process down with it is worse.
    monkeypatch.setattr(sys, "stdout", object())
    use_utf8()


def test_each_provider_answers_for_its_own_voices(tmp_path: Path):
    """What "the voices" means differs in kind by platform.

    macOS has a dozen built into `say`; Piper has whatever model files are on
    disk, and the runtime ships exactly one; silence has none. Asking the
    machine one way — reading `say -v ?` — gives an empty list on Windows and
    Linux, and 「换个女声」 there quietly does nothing.
    """
    from doc2video.tools.tts.base import TTSProvider
    from doc2video.tools.tts.providers import SilentProvider

    assert TTSProvider().voices() == []
    assert SilentProvider().voices() == []

    runtime = tmp_path / "runtime"
    (runtime / "voices").mkdir(parents=True)
    (runtime / "voices" / "zh_CN-huayan-medium.onnx").write_bytes(b"model")
    piper = PiperProvider(Settings(storage_dir=tmp_path / "data", node_dir=runtime / "node"))
    assert piper.voices() == ["zh_CN-huayan-medium"]


def test_the_automatic_order_stays_local_and_leads_with_say():
    """What "best" is, after a measurement and an ear disagreed.

    Kokoro varies its pauses five times as much as `say` (0.66 against 0.13),
    which is the measurable half of sounding like a person rather than a
    punctuation table — and on that number this order had it first. Listening
    settled it the other way for Mandarin narration: Kokoro carries an accent,
    and for explaining a deck an even, accentless delivery wins over a livelier
    foreign-sounding one. The metric was necessary and could not hear an
    accent; nothing measurable here could have.

    Kokoro keeps the place it earns: ahead of Piper, which is what Windows and
    Linux choose between.
    """
    from doc2video.tools.tts.kokoro import VOICES, KokoroProvider
    from doc2video.tools.tts.providers import AUTO_ORDER

    names = [cls.name for cls in AUTO_ORDER]
    assert names.index("macos_say") < names.index("kokoro") < names.index("piper")

    # And the automatic choice never reaches for the network. Everything in
    # `AUTO_ORDER` runs on the machine it is installed on; a video that cannot
    # be made on a train is a different product, so the networked voice has to
    # be asked for by name.
    assert "edge" not in names

    provider = KokoroProvider()
    if not provider.available():
        assert provider.voices() == []
        assert "kokoro" in provider.unavailable_reason()
    else:
        assert set(provider.voices()) == set(VOICES)


def test_the_broadcast_voice_is_asked_for_and_degrades_locally():
    """Chosen by ear over everything local, and it lives somewhere else.

    Losing the network at page nine of thirty should cost that page its
    intended voice, not cost the run — so a failure switches to the best local
    voice and records a degradation, which is how the rest of this project
    handles "the better path is unavailable".
    """
    import inspect

    from doc2video.tools.tts import TTSTool
    from doc2video.tools.tts.edge import DEFAULT_VOICE, EdgeProvider

    assert DEFAULT_VOICE == "zh-CN-YunyangNeural"
    # Slower than its own default: eight percent under is where it stopped
    # sounding hurried.
    assert EdgeProvider.natural_rate < 1.0

    body = inspect.getsource(TTSTool._speak_once)
    assert "record_degradation" in body
    assert "_local_fallback" in body


def test_an_unknown_voice_falls_back_rather_than_failing():
    """A voice name from another engine must not take the render down."""
    from doc2video.tools.tts.kokoro import DEFAULT_VOICE, VOICES

    assert DEFAULT_VOICE in VOICES


def test_each_engine_declares_its_own_comfortable_pace():
    """`1.0` does not mean the same thing to two engines.

    Measured on the same sentence: `say` lands near 266 characters a minute at
    its own default, Kokoro near 316 — fast enough to be the complaint people
    actually make. So a request like 「慢一点」 is applied on top of what the
    engine calls normal, not instead of it.
    """
    from doc2video.tools.tts.kokoro import KokoroProvider
    from doc2video.tools.tts.providers import MacOSSayProvider

    assert KokoroProvider.natural_rate < MacOSSayProvider.natural_rate == 1.0
    # 「慢一点」 on a fast engine has to end up slower than normal on it.
    assert KokoroProvider.natural_rate * 0.9 < KokoroProvider.natural_rate


def test_the_upgrade_is_a_command_and_not_something_a_render_does():
    """Several hundred megabytes fetched mid-render looks exactly like a hang.

    The same rule the Piper voice download follows: the provider reports itself
    unavailable and points at a command, rather than fetching on its own.
    """
    import inspect

    from doc2video import cli

    assert "voice-upgrade" in inspect.getsource(cli.main)
    body = inspect.getsource(cli.cmd_voice_upgrade)
    # Says what it costs before it spends it — both the megabytes and, for the
    # networked one, that it is networked at all.
    assert "400MB" in body
    assert "要联网" in body

    # And it can actually put a package where it needs to go. The first version
    # shelled out to `python -m pip`, which a uv-made environment does not have
    # — it failed on the developer's own checkout, which is where it was going
    # to fail for everyone.
    from doc2video.tools.tts.install import install_into_runtime

    # Shared with the window, which offers the same thing: two copies would
    # have drifted, and the second one would have had its own bugs.
    installer = inspect.getsource(install_into_runtime)
    assert "uv" in installer and "ensurepip" in installer


def test_the_same_project_fingerprints_the_same_before_and_after_speaking(settings, store):
    """A clip is reused when its fingerprint matches. It has to be one value.

    `provider_name` is whichever engine is loaded at this instant, and that
    changes the moment something is synthesised — a fresh tool reports
    `macos_say` and the same tool one clip later reports `edge`. Taken from
    it, a project's fingerprints disagreed with themselves between runs: one
    page was re-voiced and re-rendered on every unrelated edit. Measured on a
    real project — redoing page 5 brought page 2 back with it, 12.6 seconds of
    encoding for a page nobody had touched.
    """
    from doc2video.schemas import Scene, Source, SourceType, VideoProject
    from doc2video.skills.base import SkillContext
    from doc2video.skills.voice import VoiceSkill

    project = VideoProject(
        project_id="proj_fp",
        source=Source(type=SourceType.PPTX, file="d.pptx", path="source/d.pptx"),
    )
    project.intent.voice = "zh-CN-YunyangNeural"  # Edge's, whatever is loaded
    project.scenes = [Scene(scene_id="scn_01", source_page=1, narration="一句话。")]

    skill = VoiceSkill(SkillContext.build(project, store=store, settings=settings))
    before = skill._fingerprint(project.scenes[0])

    # What speaking does to the tool: the engine that owns the voice is loaded.
    skill.tts._provider = skill.tts._engine_for(project.intent.voice)
    assert skill._fingerprint(project.scenes[0]) == before
