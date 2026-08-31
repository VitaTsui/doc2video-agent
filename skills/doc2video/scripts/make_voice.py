#!/usr/bin/env python3
"""逐场配音，并把量出来的时长写成这支片子的时间轴。

**这一步定时间轴，不是估时间轴。** 参考的那条 SRT 链路以字幕时间为唯一权威，
因为它的音频是现成的；我们的音频是自己配的，所以顺序反过来：先合成，量出每
一场真正多长，画面再照着这个长度做。

好处是省掉了「讲稿超预算 → 重写 → 重配」这个循环——写多长都行，画面会跟上。
代价是场景组件必须在配音之后写，不能并行。

改一场只重配一场（`--scenes 3 7`）。其余场次的 wav 和时间轴原样保留，但后面
场次的起点会跟着前移或后移——所以改完之后注册表要重新生成一次。
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from lib.workspace import (  # noqa: E402
    VOICE,
    VOICEMAP,
    Meta,
    bootstrap,
    chars,
    load_storyboard,
    public_dir,
    require_engine,
)

# 一条字幕最多几个字。超过这个数，观众读不完就换下一条了。
SUBTITLE_CHARS = 26

# 场与场之间的一口气。没有它，上一场最后一个字和下一场第一个字贴在一起，
# 听起来像一句话被切断了。
SCENE_GAP = 0.35


def main() -> int:
    parser = argparse.ArgumentParser(description="逐场配音，产出时间轴")
    parser.add_argument("--out", required=True, type=Path, help="工作目录")
    parser.add_argument(
        "--scenes",
        nargs="*",
        default=None,
        help="只重配这几场（按 1 起的序号）。不给就是全配。",
    )
    parser.add_argument(
        "--allow-fallback-voice",
        action="store_true",
        help="播音腔不可用时换个声音接着跑。默认拒绝——换声音这支片子就不是一个人在讲了。",
    )
    args = parser.parse_args()

    work = bootstrap(args.out)
    require_engine()
    meta = Meta.load(work)
    board = load_storyboard(work)
    scenes = board["scenes"]

    from doc2video.tools.tts import TTSTool, voices_available

    tts = TTSTool()
    voice = meta.voice or VOICE
    if not args.allow_fallback_voice:
        _require_voice(voices_available, voice)

    audio_dir = public_dir(work) / "audio"
    audio_dir.mkdir(parents=True, exist_ok=True)

    only = _selected(args.scenes, len(scenes))
    previous = _previous_map(work)

    voiced: list[dict] = []
    for index, scene in enumerate(scenes, start=1):
        scene_id = scene["id"]
        target = audio_dir / f"{scene_id}.wav"
        keep = only is not None and index not in only

        if keep and scene_id in previous and target.exists():
            voiced.append(dict(previous[scene_id]))
            print(f"  {scene_id} 保留（{previous[scene_id]['duration']:.2f}s）", file=sys.stderr)
            continue

        narration = (scene.get("narration") or "").strip()
        if not narration:
            raise SystemExit(f"{scene_id} 没有讲稿。每一场都要有——B-roll 也要，它只是画面不同。")

        print(f"  {scene_id} 合成中…", file=sys.stderr)
        # 断句要自己给。不给的话引擎把整段当一句，回来的 segments 只有一条，
        # 于是字幕是一整段五十个字压在屏幕下沿——渲出来是对的，也是没法读的。
        result = tts.synthesize(narration, target, sentences=_sentences(narration), voice=voice)
        voiced.append(
            {
                "id": scene_id,
                "audio": f"audio/{target.name}",
                # 配的是哪段话。register_scenes.py 拿它比对分镜——改了讲稿
                # 忘了重配的话，成片会是画面新、声音和字幕旧，而三者都「成功」了。
                "narrationHash": narration_hash(narration),
                "duration": round(result.duration, 3),
                "timingSource": result.timing_source,
                "segments": [
                    {
                        "text": segment.text,
                        "start": round(segment.start, 3),
                        "end": round(segment.end, 3),
                    }
                    for segment in result.segments
                ],
            }
        )

    _lay_out(voiced)
    total = voiced[-1]["start"] + voiced[-1]["duration"] if voiced else 0.0
    payload = {
        "voice": voice,
        "gap": SCENE_GAP,
        "total": round(total, 3),
        "scenes": voiced,
    }
    (work / VOICEMAP).write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )

    estimated = sum(1 for s in voiced if s.get("timingSource") == "estimate")
    print(
        f"\n{len(voiced)} 场配完，成片 {total:.1f} 秒（{total / 60:.1f} 分钟）\n"
        f"时间轴写进 {work / VOICEMAP}\n"
        + (
            f"⚠️ {estimated} 场的句子时间是估的，不是引擎报的——字幕可能对不齐口型。\n"
            if estimated
            else ""
        )
        + "\n接下来按 references/scene-creator.md 逐场写画面。每场多长在这份文件里。"
    )
    return 0


def narration_hash(text: str) -> str:
    """一段讲稿的身份。空白不算数——重排换行不该判成改了内容。"""
    return hashlib.sha1("".join(text.split()).encode("utf-8")).hexdigest()[:12]


def _sentences(text: str) -> list[str]:
    """切成一条字幕装得下的句子。

    两级：先按句末标点断句，再把仍然过长的按逗号、顿号断开。只按句号断是不够
    的——播音腔一句话四十个字很常见，而四十个字的字幕在屏幕下沿是一堵墙。

    切分只影响字幕和时间点，不影响念出来的内容：合成用的仍是整段原文。
    """
    pieces: list[str] = []
    for sentence in re.split(r"(?<=[。！？!?])", text):
        sentence = sentence.strip()
        if not sentence:
            continue
        if chars(sentence) <= SUBTITLE_CHARS:
            pieces.append(sentence)
            continue
        # 逗号处再断，断完仍长就认了——硬切会把词切成两半，比长一点更难读。
        buffer = ""
        for clause in re.split(r"(?<=[，、；,;])", sentence):
            if chars(buffer) + chars(clause) > SUBTITLE_CHARS and buffer:
                pieces.append(buffer)
                buffer = clause
            else:
                buffer += clause
        if buffer.strip():
            pieces.append(buffer.strip())
    return pieces or [text]


def _require_voice(lookup, voice: str) -> None:
    """播音腔不可用就停在这里，别让它悄悄换个声音把整片配完。"""
    try:
        available = voice in lookup()
    except Exception:  # noqa: BLE001 - 探测失败也当作不可用
        available = False
    if not available:
        raise SystemExit(
            f"配音引擎里没有 {voice}。它是这个技能包的定义之一，不会自己换。\n"
            "先跑 check_env.py 看是装不上还是连不通；确实要换声音就加 "
            "--allow-fallback-voice。"
        )


def _selected(raw: list[str] | None, count: int) -> set[int] | None:
    if raw is None:
        return None
    picked = set()
    for item in raw:
        try:
            index = int(item)
        except ValueError:
            raise SystemExit(f"--scenes 要给序号，{item!r} 不是") from None
        if not 1 <= index <= count:
            raise SystemExit(f"没有第 {index} 场，分镜里只有 {count} 场")
        picked.add(index)
    return picked


def _previous_map(work: Path) -> dict[str, dict]:
    path = work / VOICEMAP
    if not path.exists():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return {}
    return {row["id"]: row for row in data.get("scenes", [])}


def _lay_out(voiced: list[dict]) -> None:
    """把每一场按顺序摆到时间轴上。起点永远是重新算的。

    保留下来的场次也要重新摆：它前面那一场如果改短了，它就得跟着前移。上一版
    在这里出过错——保留的场次连 `start` 一起保留，于是改完第 3 场之后，第 4 场
    往后全部和自己的配音错开半秒。
    """
    cursor = 0.0
    for row in voiced:
        row["start"] = round(cursor, 3)
        cursor += row["duration"] + SCENE_GAP


if __name__ == "__main__":
    raise SystemExit(main())
