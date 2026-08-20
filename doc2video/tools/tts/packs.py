"""The voices this machine can speak with, as something to choose from.

Three engines with nothing in common: one built into macOS, one a neural model
that has to be downloaded, one a service on someone else's computer. A person
choosing a voice does not care which of those it is — they care what it sounds
like, whether it is installed, and what installing costs. So the difference is
described here rather than shown.

Two things are said plainly because they change what someone would pick: the
size of the download, and whether the voice needs the network. A product that
works on a train is not the same product as one that does not, and that is the
user's call to make, not ours to make quietly.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from ...core.config import Settings, get_settings


@dataclass
class VoicePack:
    """One engine, as a thing to be chosen and possibly installed."""

    id: str
    name: str
    note: str
    # Roughly what installing costs, in bytes. Zero for what is already there.
    size: int
    online: bool
    installed: bool
    voices: list[dict] = field(default_factory=list)
    # What to install, when it is not installed. Empty when nothing can be.
    packages: list[str] = field(default_factory=list)
    # How to add a voice of your own to this pack, in one line. The four packs
    # have four different answers and none of them is "the same format": one
    # is a service with no local model, one downloads its own weights, one is
    # the operating system's, and one — Piper — is the only one that is a file
    # you can put in a folder.
    how: str = ""
    # That folder, when there is one. Empty for the rest.
    folder: str = ""


def catalogue(settings: Settings | None = None) -> list[VoicePack]:
    """Every pack, installed or not, best first."""
    from .. import tts
    from .edge import DEFAULT_VOICE as EDGE_DEFAULT
    from .edge import EdgeProvider
    from .kokoro import KokoroProvider
    from .piper import PiperProvider, engine_present
    from .providers import MacOSSayProvider

    settings = settings or get_settings()

    def described(provider, gendered: bool = True) -> list[dict]:
        return [
            {
                "id": voice,
                "name": voice.split("(")[0].strip(),
                "gender": tts.gender_of(voice) if gendered else None,
            }
            for voice in provider.voices()
        ]

    edge, kokoro = EdgeProvider(), KokoroProvider()
    system, piper = MacOSSayProvider(), PiperProvider(settings)

    packs = [
        VoicePack(
            id="edge",
            name="播音腔",
            note=f"新闻播报的声音，默认 {EDGE_DEFAULT.split('-')[-1]}。合成时需要联网。",
            size=1_000_000,
            online=True,
            installed=edge.available(),
            voices=described(edge),
            packages=["edge-tts"],
            how="云端音色，没有可下载的模型文件，也加不了自己的。",
        ),
        VoicePack(
            id="kokoro",
            name="本地神经语音",
            note="全程离线，停顿比系统声音自然，但带一点口音。",
            size=400_000_000,
            online=False,
            installed=kokoro.available(),
            voices=described(kokoro),
            packages=["kokoro", "misaki[zh]"],
            how="音色是模型自带的这八个，装上就有，不单独加。",
        ),
        VoicePack(
            id="system",
            name="系统自带",
            note="装完就有，不联网，语速平稳。",
            size=0,
            online=False,
            installed=system.available(),
            voices=described(system),
            how="想要更多：系统设置 → 辅助功能 → 朗读内容 → 系统声音，下载完这里自动出现。",
        ),
        VoicePack(
            id="piper",
            name="Piper",
            note="Windows / Linux 的兜底声音，随运行时一起装好。",
            size=0,
            online=False,
            installed=piper.available(),
            voices=described(piper, gendered=False),
            # The one pack that is a file format. Voices are ONNX models —
            # rhasspy/piper-voices on HuggingFace has a few hundred — and the
            # provider already picks up whatever is in this directory. That
            # has been true and undiscoverable: nothing in the window said the
            # folder existed.
            how="把 .onnx 和同名 .onnx.json 放进下面这个文件夹，重开设置就在了。"
            "音色可以从 HuggingFace 的 rhasspy/piper-voices 下载。",
            folder=str(settings.storage_dir / "voices"),
        ),
    ]
    # A pack with no voices on this machine is not a choice; it is a line of
    # text explaining something the reader cannot act on. Piper is the
    # exception in one direction: with its engine present but no voice file
    # yet, the pack is exactly what someone needs to see — that is the state
    # its downloader exists for. With the engine absent (macOS: the wheel's
    # espeak data path is compiled in as the build machine's, re-checked on
    # 1.7.x today) it stays hidden, because a voice downloaded there could not
    # be spoken.
    return [
        pack
        for pack in packs
        if pack.installed or pack.packages or (pack.id == "piper" and engine_present())
    ]


def in_use(settings: Settings | None = None) -> tuple[str, str]:
    """The engine and voice a video would be made with right now.

    Not the same as the configured value: `tts_voice` is empty by default and
    means "whatever this machine settles on", which is a real answer but not
    one anybody can read. So it is resolved the way a render resolves it — the
    engine that would run, and the voice it would use if nobody named one.

    A project can still say otherwise (「用播音腔讲」 sets its own), and that
    belongs to the video rather than to the machine. This is the default it
    starts from.
    """
    from ...core import prefs
    from . import TTSTool

    settings = settings or get_settings()
    chosen = prefs.load(settings).voice
    tool = TTSTool(settings)
    if chosen:
        return tool._engine_for(chosen).name, chosen  # noqa: SLF001
    engine = tool._engine_for(tool.voice)  # noqa: SLF001 - the same resolution a render does

    # Empty when the engine has no default of its own: `say` with no `-v`
    # speaks in whatever voice macOS is set to, and naming the first of its
    # list here would be inventing an answer.
    return engine.name, tool.voice or engine.default_voice


# The engine each pack is, by the name the engine calls itself. Two names for
# one thing because one is a product and the other is a module: 「系统自带」 is
# what a person picks, `macos_say` is what runs.
PACK_OF_ENGINE = {"edge": "edge", "kokoro": "kokoro", "macos_say": "system", "piper": "piper"}


def payload(settings: Settings | None = None) -> dict:
    from ...core import prefs

    settings = settings or get_settings()
    packs = catalogue(settings)
    provider, voice = in_use(settings)
    return {
        # What was chosen in the window, or configured on the machine — often
        # neither…
        "current": prefs.load(settings).voice or settings.tts_voice,
        # …and what that actually comes out as, which is what someone asking
        # 「现在用的是哪个声音」 wants to know.
        "provider": provider,
        # Which pack that engine is, so the window can name it the way it
        # names it everywhere else rather than printing the module name.
        "pack": PACK_OF_ENGINE.get(provider, ""),
        "voice": voice,
        "packs": [
            {
                "id": pack.id,
                "name": pack.name,
                "note": pack.note,
                "size": pack.size,
                "online": pack.online,
                "installed": pack.installed,
                "voices": pack.voices,
                "how": pack.how,
                "folder": pack.folder,
            }
            for pack in packs
        ],
    }
