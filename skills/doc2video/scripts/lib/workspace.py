"""工作目录的约定，以及在 import doc2video 之前把环境定死。

两件事只在这里做一次，其余脚本都从这里拿：

**环境变量必须早于 import。** ``get_settings`` 是 ``lru_cache`` 的，进程里第一次
读到什么就是什么。配音要用播音腔、工程要落在工作目录下，都得在引擎被导入之前
写进 ``os.environ``——晚一步就成了「设了但没生效」，而这种失败是静默的：视频照样
出，只是换了个声音、工程落在了别处。

**工作目录的文件名只在这里定义。** 五个脚本读写同一批文件，名字散在各处的话，
改一个就得记得改另外四个。
"""

from __future__ import annotations

import json
import os
import shutil
import sys
from dataclasses import dataclass
from pathlib import Path

#: 播音腔。这个技能包只用这一个音色——Microsoft 给它的标签是 Professional /
#: Reliable，是这一组中文音色里唯一是这个性格的（其余是 Warm / Lively / Cute /
#: Passion）。讲一份材料，这个差别就是全部。引擎按音色名认引擎：
#: ``zh-CN-*Neural`` 是 edge 的，所以指定音色就等于指定了 edge。
VOICE = "zh-CN-YunyangNeural"

# 工作目录里的文件名。中文，因为这些文件是给人和模型读的，不是给程序认的。
#
# 这里没有「页面.md」也没有「讲稿/」的位置：画面是生成的，不是文档页面，
# 所以素材是内容、分镜是内容的重新组织，两者都不进画面。
MATERIAL = "素材.md"
STORYBOARD = "分镜.json"
VOICEMAP = "配音.json"
PROJECT = "项目"
META = "工程.json"
STATUS = "状态.json"
LOG = "运行.log"
VIDEO = "成片.mp4"
STORE = "工程库"


def bootstrap(work: Path) -> Path:
    """把环境定死，返回工作目录的绝对路径。**必须在 import doc2video 之前调用。**"""
    work = Path(work).expanduser().resolve()
    # 工程、页面图、每页配音、片段、成片全落在工作目录下。默认是 ./storage，
    # 那会跟着当前目录跑——两个脚本在不同目录下启动就会写进两个工程库，
    # 第二步找不到第一步建的工程。
    os.environ["D2V_STORAGE_DIR"] = str(work / STORE)
    # 播音腔是这个技能包的定义之一。改一场重配一场的时候，如果这里换了声音，
    # 成片里就有一场是别人在讲——而听感上「还行」，没人会回头查。
    os.environ["D2V_TTS_PROVIDER"] = "edge"
    os.environ["D2V_TTS_VOICE"] = VOICE
    # 讲稿由模型写，引擎这边一个模型都不调。默认本来就是 mock，写死是防着
    # 环境里恰好有 ANTHROPIC_API_KEY——那会让它自己去写，而这正是要避免的。
    os.environ["D2V_LLM_PROVIDER"] = "mock"
    # 字幕要中文字体。技能包的 vendor/fonts/ 里放了字体就用它——沙箱里往往
    # 一个中文字体都没有，而没有的后果是字幕整片跳过（0.10.31 之前是烧成方块）。
    if "D2V_FONT_PATH" not in os.environ and (font := _bundled_font()):
        os.environ["D2V_FONT_PATH"] = str(font)
    work.mkdir(parents=True, exist_ok=True)
    return work


def _bundled_font() -> Path | None:
    folder = Path(__file__).resolve().parents[2] / "vendor" / "fonts"
    if not folder.is_dir():
        return None
    for suffix in ("*.otf", "*.ttf", "*.ttc"):
        found = sorted(folder.glob(suffix))
        if found:
            return found[0]
    return None


def bundled_version() -> str | None:
    """技能包自带的引擎是哪个版本。"""
    wheel = bundled_wheel()
    return wheel.name.split("-")[1] if wheel else None


def bundled_wheels() -> list[Path]:
    """vendor 里所有的引擎 wheel，新的在前。

    **按版本排，不按文件名排。** 字典序里 "0.10.30" 排在 "0.10.31" 前面是对的，
    但 "0.10.9" 会排到 "0.10.30" 后面——升到两位数小版本的那天就错了。线上真
    撞到过一次同类的：目录里躺着 0.10.30 和 0.10.31 两个 wheel，取到旧的那个，
    于是版本闸拿一个不存在的「自带版本」去比对。
    """
    folder = Path(__file__).resolve().parents[2] / "vendor"
    wheels = [w for w in folder.glob("doc2video_agent-*.whl") if len(w.name.split("-")) >= 2]
    return sorted(wheels, key=lambda w: version_tuple(w.name.split("-")[1]), reverse=True)


def bundled_wheel() -> Path | None:
    """该装的那一个 wheel。"""
    wheels = bundled_wheels()
    return wheels[0] if wheels else None


def install_command(*, force: bool = False) -> str:
    """装引擎的那条命令，指名到具体文件。

    不能用 `doc2video_agent-*.whl`：vendor 里只要留着两个版本的 wheel，通配符
    就展开成两个文件，pip 试图同时安装两个版本，报的是

        ERROR: Cannot install doc2video-agent 0.10.30 and doc2video-agent 0.10.31
        because these package versions have conflicting dependencies.
        ERROR: ResolutionImpossible

    看起来像依赖冲突，其实是命令写错了。线上真的被它拦下过一次。
    """
    wheel = bundled_wheel()
    target = str(wheel) if wheel else str(
        Path(__file__).resolve().parents[2] / "vendor" / "doc2video_agent-<版本>-py3-none-any.whl"
    )
    flag = "--force-reinstall " if force else ""
    return f"pip install {flag}{target} --no-deps"


def version_tuple(version: str) -> tuple[int, ...]:
    out = []
    for piece in version.split("."):
        digits = "".join(c for c in piece if c.isdigit())
        out.append(int(digits) if digits else 0)
    return tuple(out)


def installed_version() -> str | None:
    """环境里装着的引擎是哪个版本。问不出来就是 None——不为了检查本身挡住人。

    `doc2video.__version__` 不能用，它常年停在 0.1.0；发行版本在包元数据里。
    """
    try:
        from importlib.metadata import version

        return version("doc2video-agent")
    except Exception:  # noqa: BLE001
        return None

def require_engine() -> None:
    """确认 doc2video 装上了、而且是这一版脚本认识的那个。

    版本要查，是因为「装过了」和「装的是对的那个」在这里是两件事。这些脚本按
    技能包自带的那个 wheel 的 API 写，而机器上很可能已经有另一个 doc2video——
    比如桌面 app 的运行时。那种情况下脚本会走到一半才炸在某个 ImportError 上，
    报的是「cannot import name ...」，看起来像技能包坏了。
    """
    here = Path(__file__).resolve().parents[2]
    install = f"    {install_command()}\n    pip install -r {here / 'requirements.txt'}"
    try:
        import doc2video  # noqa: F401
    except ImportError as exc:
        print(
            f"没有找到 doc2video 引擎。先装它，再跑这个脚本：\n{install}\n"
            "装完用 python3 scripts/check_env.py 确认。",
            file=sys.stderr,
        )
        raise SystemExit(1) from exc

    want = bundled_version()
    if want is None:
        return
    have = installed_version()
    if have is None:
        return

    if have == want:
        return
    if version_tuple(have) < version_tuple(want):
        print(
            f"引擎版本对不上：装着的是 {have}，这套脚本要 {want}。\n"
            "低版本会在链路中间炸在某个 ImportError 上，先换成技能包自带的：\n"
            f"    {install_command(force=True)}",
            file=sys.stderr,
        )
        raise SystemExit(1)
    print(
        f"⚠️ 引擎是 {have}，比技能包自带的 {want} 新。多半没事，"
        "出怪事就换回自带的那个。",
        file=sys.stderr,
    )


@dataclass
class Meta:
    """工作目录的元数据：脚本之间只靠它传工程 id，不靠调用方记。"""

    project_id: str
    source: str
    brief: str
    voice: str = VOICE

    @classmethod
    def load(cls, work: Path) -> Meta:
        path = work / META
        if not path.exists():
            raise SystemExit(f"{path} 不存在——先跑 prepare.py，它会建出这个工作目录")
        return cls(**json.loads(path.read_text(encoding="utf-8")))

    def save(self, work: Path) -> None:
        (work / META).write_text(
            json.dumps(self.__dict__, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
        )


def write_status(work: Path, **fields) -> None:
    """状态文件是后台任务与轮询之间唯一的接口，整份重写而不是追加。"""
    path = work / STATUS
    now = dict(json.loads(path.read_text(encoding="utf-8"))) if path.exists() else {}
    now.update(fields)
    path.write_text(json.dumps(now, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def read_status(work: Path) -> dict:
    path = work / STATUS
    if not path.exists():
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except ValueError:
        return {}


def chars(text: str) -> int:
    """字数按引擎估时长的口径数：只数汉字。

    标点不占时间（它占的是停顿，另有账），英文按词算不按字母算——所以这里数出来
    的数字和「文件有多少字符」不是一回事。只用在 ``validate_storyboard.py`` 的
    粗估和字幕断句上；真实时长以合成结果为准。
    """
    return sum(1 for ch in text if "一" <= ch <= "鿿")


def project_dir(work: Path) -> Path:
    """Remotion 工程所在。音频、B-roll 素材都要落在它的 public/ 下才读得到。"""
    return work / PROJECT


def public_dir(work: Path) -> Path:
    return project_dir(work) / "public"


def scenes_dir(work: Path) -> Path:
    """生成的场景组件放这里，一场一个文件。"""
    return project_dir(work) / "src" / "scenes"


def load_storyboard(work: Path) -> dict:
    path = work / STORYBOARD
    if not path.exists():
        raise SystemExit(
            f"{path} 不存在。分镜是你写的，不是脚本生成的——"
            "读 素材.md，按 references/storyboard.md 的契约写出来。"
        )
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise SystemExit(f"{path} 不是合法 JSON：{exc}") from None
    if not isinstance(data, dict) or not isinstance(data.get("scenes"), list):
        raise SystemExit(f"{path} 里没有 scenes 数组")
    return data


def load_voicemap(work: Path) -> dict:
    path = work / VOICEMAP
    if not path.exists():
        raise SystemExit(f"{path} 不存在——先跑 make_voice.py，时间轴由配音定")
    return json.loads(path.read_text(encoding="utf-8"))


def scene_component(scene_id: str) -> str:
    """scene-007 → Scene007。组件名和文件名都用它，注册表也认这个。"""
    tail = scene_id.rsplit("-", 1)[-1]
    return f"Scene{tail.zfill(3)}"


def node_bin(name: str) -> str:
    """npm / npx 的真实路径。

    Windows 上它们是 npm.cmd / npx.cmd，而 subprocess 不走 PATHEXT——直接传
    "npx" 会以 WinError 2「系统找不到指定的文件」失败，看起来像没装 Node。
    沙箱是 Linux，这里主要是为了让本机排查时不撞上这一下。
    """
    found = shutil.which(name)
    if found is None:
        raise SystemExit(
            f"没有找到 {name}。这条链路的画面是 Remotion 渲的，Node 是硬依赖——"
            "装 Node 18+ 之后再跑。"
        )
    return found
