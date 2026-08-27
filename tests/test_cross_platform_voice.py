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
from doc2video.tools.tts.providers import AUTO_ORDER, SilentProvider, resolve_provider


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


def test_a_mute_film_is_reported_rather_than_scored_full_marks(tmp_path: Path, settings, store):
    """The one defect the review could not see.

    The silent provider writes a real clip of exactly the right length, so a
    machine with no voice produced a film where every check passed — correct
    timeline, correct subtitles, full marks — and no sound at all. Degrading to
    silence is right; scoring it as a finished video is not.
    """
    from doc2video.schemas import Scene, SceneAudio, Source, SourceType, VideoProject
    from doc2video.skills.base import SkillContext
    from doc2video.skills.review import ReviewSkill

    project = VideoProject(
        project_id="proj_mute",
        source=Source(type=SourceType.PPTX, file="d.pptx", path="source/d.pptx"),
    )
    clip = store.audio_dir(project.project_id) / "scn_1.wav"
    clip.parent.mkdir(parents=True, exist_ok=True)
    SilentProvider().synthesize("这一页讲的是产业链经营。", clip)
    project.scenes = [
        Scene(
            scene_id="scn_1",
            source_page=1,
            narration="这一页讲的是产业链经营。",
            duration=3.0,
            audio=SceneAudio(path=f"audio/{clip.name}", duration=3.0, provider=SilentProvider.name),
        )
    ]

    skill = ReviewSkill(SkillContext.build(project, store=store, settings=settings))
    findings = skill._structural_checks()
    silent = [f for f in findings if f.kind == "silent_audio"]
    assert silent, "哑片必须被报出来"
    assert "静音占位" in silent[0].message


def test_what_stopped_the_voice_is_named_when_it_falls_through_to_silence(monkeypatch, caplog):
    """A mute film is worth one loud sentence saying which engines refused.

    Without it the only evidence is a video nobody can hear, and the fix
    (install a voice) is not guessable from that.
    """
    import logging

    from doc2video.tools.tts import providers

    monkeypatch.setattr(providers.MacOSSayProvider, "available", lambda self: False)
    monkeypatch.setattr(providers.KokoroProvider, "available", lambda self: False)
    monkeypatch.setattr(providers.PiperProvider, "available", lambda self: False)
    monkeypatch.setattr(providers.PiperProvider, "unavailable_reason", lambda self: "模型未安装")

    with caplog.at_level(logging.WARNING):
        provider = resolve_provider("auto")

    assert provider.name == SilentProvider.name
    said = " ".join(record.getMessage() for record in caplog.records)
    assert "没有可用的配音引擎" in said
    assert "模型未安装" in said, "要说清楚是哪一个引擎因为什么不能用"


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


def test_the_published_voices_can_be_searched_by_what_a_person_would_type(tmp_path, settings):
    """174 voices is a list you search, not one you read.

    Matched against everything someone might reach for: 「中文」, `zh`,
    `Chinese` and the speaker's own name all have to find the same voices.
    """
    import json

    from doc2video.tools.tts import piper_catalogue

    settings = settings.model_copy(update={"storage_dir": tmp_path})
    (tmp_path / "voices").mkdir(parents=True)
    (tmp_path / "voices" / piper_catalogue.INDEX_FILE).write_text(
        json.dumps(
            {
                "zh_CN-huayan-medium": {
                    "key": "zh_CN-huayan-medium",
                    "name": "huayan",
                    "quality": "medium",
                    "language": {
                        "code": "zh_CN",
                        "name_native": "简体中文",
                        "name_english": "Chinese",
                        "country_english": "China",
                    },
                    "files": {"zh/zh_CN-huayan-medium.onnx": {"size_bytes": 63201294}},
                },
                "en_US-amy-low": {
                    "key": "en_US-amy-low",
                    "name": "amy",
                    "quality": "low",
                    "language": {
                        "code": "en_US",
                        "name_native": "English",
                        "name_english": "English",
                        "country_english": "United States",
                    },
                    "files": {"en/en_US-amy-low.onnx": {"size_bytes": 60000000}},
                },
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )

    for query in ("中文", "zh", "Chinese", "huayan"):
        found = piper_catalogue.search(query, settings=settings)
        assert [v["key"] for v in found["voices"]] == ["zh_CN-huayan-medium"], query

    everything = piper_catalogue.search("", settings=settings)
    assert everything["total"] == 2
    # The size is the model's, so the button can say what the download costs.
    sizes = {v["key"]: v["size"] for v in everything["voices"]}
    assert sizes["zh_CN-huayan-medium"] == 63201294
    assert all(not v["installed"] for v in everything["voices"])

    # A model file in the folder is what "installed" means — the provider
    # speaks with whatever is there, so this is the same question it asks.
    (tmp_path / "voices" / "en_US-amy-low.onnx").write_bytes(b"")
    assert piper_catalogue.search("amy", settings=settings)["voices"][0]["installed"]


def test_one_timeout_costs_one_clip_rather_than_the_rest_of_the_deck(tmp_path, monkeypatch):
    """`say` hung once on page four and the film went mute from there.

    Falling back to another *voice* is meant to be permanent — a film half in
    one voice and half in another is worse than one consistently in the second.
    Falling back to *silence* is not that: silence is the absence of a voice,
    and making it the current engine meant every later page started from it.
    Twenty-seven of thirty pages came out mute from a single timeout.
    """
    import struct
    import wave

    from doc2video.core.config import Settings
    from doc2video.tools.tts import TTSTool
    from doc2video.tools.tts.base import TTSProvider

    spoken: list[str] = []

    class Flaky(TTSProvider):
        name = "flaky"
        honours_phrase_boundary = False

        def available(self) -> bool:
            return True

        def synthesize(self, text, out_path, *, voice="", rate=1.0):
            spoken.append(text)
            # Every attempt at the second clip fails; everything else is fine.
            if "第二句" in text:
                raise TimeoutError("合成超时")
            out_path.parent.mkdir(parents=True, exist_ok=True)
            with wave.open(str(out_path), "wb") as handle:
                handle.setnchannels(1)
                handle.setsampwidth(2)
                handle.setframerate(22050)
                handle.writeframes(struct.pack("<h", 1000) * 22050)
            return 1.0

    tool = TTSTool(Settings())
    engine = Flaky()
    monkeypatch.setattr(tool, "_engine_for", lambda voice: engine)
    monkeypatch.setattr(tool, "_local_fallback", lambda: SilentProvider())

    tool.synthesize("第一句话。", tmp_path / "a.wav")
    tool.synthesize("这是第二句。", tmp_path / "b.wav")
    third = tool.synthesize("第三句话。", tmp_path / "c.wav")

    assert third.provider == "flaky", "静音只该赔上失败的那一段，下一段要回到真嗓子"
    assert sum("第二句" in text for text in spoken) == 2, "超时先重试一次再放弃"


def test_a_silent_page_is_spoken_again_when_the_machine_can_speak(tmp_path):
    """Re-speaking silence produces silence — but only when silence is all there is.

    A page that came out silent while a real voice is available is the one page
    that most needs saying again: it is what a single timeout looks like.
    """
    from doc2video.core.config import Settings
    from doc2video.schemas import Scene, SceneAudio, Source, SourceType, VideoProject
    from doc2video.skills.base import SkillContext
    from doc2video.skills.voice import VoiceSkill
    from doc2video.storage import ProjectStore

    settings = Settings(storage_dir=str(tmp_path))
    store = ProjectStore(settings)
    project = VideoProject(
        project_id="proj_redo",
        source=Source(type=SourceType.PPTX, file="d.pptx", path="source/d.pptx"),
    )
    project.scenes = [
        Scene(
            scene_id="scn_1",
            source_page=1,
            narration="这一页讲的是产业链经营。",
            audio=SceneAudio(path="audio/scn_1.wav", duration=3.0, provider=SilentProvider.name),
        )
    ]
    skill = VoiceSkill(SkillContext.build(project, store=store, settings=settings))

    # macOS `say` is present in CI for this repo's other voice tests; where it
    # is not, the machine genuinely has no voice and skipping is correct.
    assert skill._has_voice() is any(
        cls.name != SilentProvider.name and cls().available() for cls in AUTO_ORDER
    )


def test_a_voice_engine_survives_an_update(monkeypatch, tmp_path):
    """Installing a voice and then updating meant installing it again.

    The engines went into the interpreter that was running, which for the
    desktop app is the downloaded runtime — and an update replaces that whole
    directory. The voices themselves were never the problem: the `.onnx` models
    live beside the projects. It is the engine that reads them that was being
    thrown away.
    """
    from doc2video.tools.tts.install import packages_dir

    monkeypatch.delenv("D2V_PACKAGES_DIR", raising=False)
    assert packages_dir() is None, "没有指定就装在原地，源码检出正是这种情况"

    monkeypatch.setenv("D2V_PACKAGES_DIR", str(tmp_path / "packages"))
    assert packages_dir() == tmp_path / "packages"


def test_what_is_installed_there_is_importable():
    """Put somewhere else and not looked for is the same as not installed."""
    import inspect

    from doc2video import cli

    source = inspect.getsource(cli._use_installed_packages)
    assert "sys.path.insert(0" in source, "要放在最前面：有意装的那个才是想用的"


def test_the_voice_listing_is_probed_once_per_process(monkeypatch):
    """`say -v ?` runs once and its answer is kept; failure is not kept.

    The listing used to be fetched fresh on every call, and `/health/voices`
    is polled by the window — so when macOS's speech daemon wedged, every poll
    spawned another probe, each one parked a backend thread in an
    uninterruptible wait, and after a few hours every endpoint that shared the
    path hung. 「历史工程点开没东西了」 was the window awaiting one of those
    requests forever.
    """
    from doc2video.tools.tts import providers

    calls = {"n": 0}

    class FakeChild:
        pid = 999999
        returncode = 0

        def communicate(self, timeout=None):  # noqa: ARG002
            calls["n"] += 1
            return ("Tingting zh_CN    # 你好\n", "")

    monkeypatch.setattr(providers, "_SAY_LISTING", None)
    monkeypatch.setattr(providers.subprocess, "Popen", lambda *a, **k: FakeChild())
    monkeypatch.setattr(providers, "which", lambda name: "/usr/bin/say")

    first = providers.MacOSSayProvider().voices()
    second = providers.MacOSSayProvider().voices()
    assert first == second == ["Tingting"]
    assert calls["n"] == 1, "第二次要走缓存，不再生子进程"

    # A failed probe is answered with an empty menu and is NOT cached: the
    # next call gets to try again.
    monkeypatch.setattr(providers, "_SAY_LISTING", None)

    class WedgedChild(FakeChild):
        def communicate(self, timeout=None):  # noqa: ARG002
            calls["n"] += 1
            raise providers.subprocess.TimeoutExpired(cmd="say", timeout=10)

    kills = {"n": 0}
    monkeypatch.setattr(providers.subprocess, "Popen", lambda *a, **k: WedgedChild())
    monkeypatch.setattr(providers.os, "killpg", lambda *a: kills.__setitem__("n", kills["n"] + 1))
    assert providers.MacOSSayProvider().voices() == []
    assert kills["n"] >= 1, "超时要杀整个进程组"
    assert providers._SAY_LISTING is None, "失败不缓存，下次还能再探"
