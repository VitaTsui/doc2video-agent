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
import sys
from dataclasses import dataclass
from pathlib import Path

#: 播音腔。这个技能包只用这一个音色——Microsoft 给它的标签是 Professional /
#: Reliable，是这一组中文音色里唯一是这个性格的（其余是 Warm / Lively / Cute /
#: Passion）。讲一份材料，这个差别就是全部。引擎按音色名认引擎：
#: ``zh-CN-*Neural`` 是 edge 的，所以指定音色就等于指定了 edge。
VOICE = "zh-CN-YunyangNeural"

# 工作目录里的文件名。中文，因为这些文件是给人和模型读的，不是给程序认的。
PAGES = "页面.md"
BUDGET = "预算.tsv"
SCRIPTS = "讲稿"
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
    # 播音腔是这个技能包的定义之一，不给覆盖的余地：换音色会连字数预算一起换
    # （每个引擎的语速不同），而讲稿已经按上一个预算写好了。
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


def require_engine() -> None:
    """确认 doc2video 装上了，没装就说清楚怎么装，而不是抛 ImportError。"""
    try:
        import doc2video  # noqa: F401
    except ImportError:
        here = Path(__file__).resolve().parents[2]
        print(
            "没有找到 doc2video 引擎。先装它，再跑这个脚本：\n"
            f"    pip install {here / 'vendor'}/doc2video_agent-*.whl\n"
            f"    pip install -r {here / 'requirements.txt'}\n"
            "装完用 python3 scripts/check_env.py 确认。",
            file=sys.stderr,
        )
        raise SystemExit(1) from None


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


def script_path(work: Path, page: int) -> Path:
    return work / SCRIPTS / f"p{page:02d}.md"


def read_scripts(work: Path) -> dict[int, str]:
    """讲稿目录 → ``{页码: 讲稿}``。空文件不算写了，直接不收进来。

    模板头（``<!-- … -->``）会被剥掉：它是写给写的人看的预算提示，念出来就成了
    「小于号叹号减减第三页目标二十四点五秒」。
    """
    out: dict[int, str] = {}
    folder = work / SCRIPTS
    if not folder.exists():
        return out
    for path in sorted(folder.glob("p*.md")):
        try:
            page = int(path.stem[1:])
        except ValueError:
            continue
        lines = [
            line
            for line in path.read_text(encoding="utf-8").splitlines()
            if not line.strip().startswith("<!--")
        ]
        text = "\n".join(lines).strip()
        if text:
            out[page] = text
    return out


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
    的数字和「文件有多少字符」不是一回事，和 ``预算.tsv`` 里的 ``目标字数``
    才是同一把尺子。英文多的一页会显得字数偏少，以 ``check_script.py`` 报的
    预计秒数为准。
    """
    return sum(1 for ch in text if "一" <= ch <= "鿿")
