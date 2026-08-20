"""Pure-ffmpeg renderer adapter.

Covers the deterministic action set with plain, well-understood filters:
page display, fade transition, Ken-Burns / zoom via ``zoompan``, highlight and
pointer via ``drawbox``, burned-in subtitles via ``drawtext``. No Node toolchain
required, which makes it the dependable fallback when Remotion is unavailable.
"""

from __future__ import annotations

from pathlib import Path

from ...core import ledger
from ...core.config import Settings
from ...core.logging import get_logger
from ...schemas import ActionType
from .. import ffmpeg, media_binaries
from ..parsers.slide_raster import font_candidates
from .base import PlanAction, RendererAdapter, ScenePlan

log = get_logger(__name__)

HIGHLIGHT_COLOR = "0xF2B705@0.85"
POINTER_COLOR = "0xE2574C@0.95"
SUBTITLE_COLOR = "white"
SUBTITLE_BOX_COLOR = "black@0.55"
BOX_BORDER = 16


class FFmpegAdapter(RendererAdapter):
    name = "ffmpeg"

    def __init__(self, settings: Settings | None = None) -> None:
        # Accepted for a uniform constructor across adapters; this one needs
        # nothing from settings.
        self.settings = settings

    def available(self) -> bool:
        return ffmpeg.available()

    def unavailable_reason(self) -> str:
        return "未安装 ffmpeg"

    def render_scene(self, plan: ScenePlan, out_path: Path) -> Path:
        if not plan.image:
            raise ValueError(f"场景 {plan.scene_id} 缺少可渲染的画面资源")
        out_path.parent.mkdir(parents=True, exist_ok=True)
        video_filter = ",".join(self._build_filters(plan))
        with ledger.call(
            f"renderer:{self.name}", plan.scene_id, covers=[ledger.scene_key(plan.scene_id)]
        ):
            ffmpeg.encode_still(
                Path(plan.image),
                out_path,
                duration=plan.duration,
                width=plan.width,
                height=plan.height,
                fps=plan.fps,
                video_filter=video_filter,
            )
        return out_path

    # -- filtergraph ---------------------------------------------------
    def _build_filters(self, plan: ScenePlan) -> list[str]:
        w, h = plan.width, plan.height
        filters = [
            f"scale={w}:{h}:force_original_aspect_ratio=decrease",
            f"pad={w}:{h}:(ow-iw)/2:(oh-ih)/2:color=white",
            "setsar=1",
        ]

        zooms = [a for a in plan.actions if a.type is ActionType.ZOOM and a.area]
        if zooms:
            filters.append(self._zoompan(plan, zooms))

        for action in plan.actions:
            if action.area is None:
                continue
            if action.type is ActionType.HIGHLIGHT:
                filters.append(self._box(action, w, h, HIGHLIGHT_COLOR, thickness=6))
            elif action.type is ActionType.POINTER:
                filters.append(self._pointer(action, w, h))

        if plan.transition_in == "fade" and plan.transition_duration > 0:
            filters.append(f"fade=t=in:st=0:d={plan.transition_duration:.2f}")

        subtitle_filters = self._subtitles(plan)
        filters.extend(subtitle_filters)
        return filters

    def _zoompan(self, plan: ScenePlan, zooms: list[PlanAction]) -> str:
        """Piecewise zoom expressed over the output frame index.

        zoompan drives everything from ``on`` (output frame number), so each
        action becomes one ``if(between(on, s, e), value, ...)`` branch. Zoom
        eases in over the first 30% of the action and holds for the rest.
        """
        fps = plan.fps
        zoom_expr = "1"
        x_expr = "iw/2-(iw/zoom/2)"
        y_expr = "ih/2-(ih/zoom/2)"

        for action in reversed(zooms):
            area = action.area
            assert area is not None
            start_f = int(action.start * fps)
            end_f = max(start_f + 1, int(action.end * fps))
            ease_f = max(1, int((end_f - start_f) * 0.3))
            # Zoom factor that makes the target area fill the frame, capped so
            # a tiny target does not turn into a pixel-mush close-up.
            target_zoom = min(3.0, max(1.05, 1.0 / max(area.w, area.h, 0.05)))
            ramp = (
                f"if(lt(on,{start_f + ease_f}),"
                f"1+({target_zoom:.4f}-1)*(on-{start_f})/{ease_f},{target_zoom:.4f})"
            )
            zoom_expr = f"if(between(on,{start_f},{end_f}),{ramp},{zoom_expr})"
            cx, cy = area.x + area.w / 2, area.y + area.h / 2
            x_expr = (
                f"if(between(on,{start_f},{end_f}),"
                f"iw*{cx:.4f}-(iw/zoom/2),{x_expr})"
            )
            y_expr = (
                f"if(between(on,{start_f},{end_f}),"
                f"ih*{cy:.4f}-(ih/zoom/2),{y_expr})"
            )

        return (
            f"zoompan=z='{zoom_expr}':x='{x_expr}':y='{y_expr}'"
            f":d=1:s={plan.width}x{plan.height}:fps={fps}"
        )

    def _box(self, action: PlanAction, w: int, h: int, color: str, *, thickness: int) -> str:
        area = action.area
        assert area is not None
        return (
            f"drawbox=x={int(area.x * w)}:y={int(area.y * h)}"
            f":w={int(area.w * w)}:h={int(area.h * h)}"
            f":color={color}:t={thickness}"
            f":enable='between(t,{action.start:.3f},{action.end:.3f})'"
        )

    def _pointer(self, action: PlanAction, w: int, h: int) -> str:
        """A small filled marker at the target's top-left — a stand-in cursor."""
        area = action.area
        assert area is not None
        size = max(12, int(min(w, h) * 0.012))
        return (
            f"drawbox=x={int(area.x * w)}:y={int(area.y * h)}:w={size}:h={size}"
            f":color={POINTER_COLOR}:t=fill"
            f":enable='between(t,{action.start:.3f},{action.end:.3f})'"
        )

    def _subtitles(self, plan: ScenePlan) -> list[str]:
        if not plan.subtitles:
            return []
        # Not every ffmpeg build compiles in drawtext; losing subtitles is far
        # better than losing the whole render.
        if not media_binaries.has_filter("drawtext"):
            log.warning(
                "当前 ffmpeg 不支持 drawtext 滤镜，跳过烧录字幕"
                "（安装系统 ffmpeg，或改用 Remotion 渲染器可恢复）"
            )
            return []
        font = _find_font()
        if font is None:
            log.warning("未找到可用字体，跳过烧录字幕")
            return []
        font_size = max(24, int(plan.height * 0.036))
        # Anchor the box to the bottom edge, not to a fixed line: the gap below
        # it is the number shared with the Remotion adapter, so the caption
        # lands in the same place whichever renderer produced the clip. The box
        # extends `boxborderw` past the text, hence the extra term.
        offset = int(plan.height * plan.subtitle_margin) + BOX_BORDER
        filters = []
        for cue in plan.subtitles:
            text = _escape_drawtext(cue.text)
            filters.append(
                # expansion=none: without it drawtext reads `%{...}` as a
                # template, and a bare `%` — every "增长 40%" in a narration —
                # makes it drop the whole cue with only a "Stray %" warning.
                f"drawtext=fontfile='{font}':expansion=none:text='{text}'"
                f":fontcolor={SUBTITLE_COLOR}:fontsize={font_size}"
                f":box=1:boxcolor={SUBTITLE_BOX_COLOR}:boxborderw={BOX_BORDER}"
                f":x=(w-text_w)/2:y=h-text_h-{offset}"
                f":enable='between(t,{cue.start:.3f},{cue.end:.3f})'"
            )
        return filters


def _find_font() -> str | None:
    """The same list the rasteriser uses — including anything the runtime ships.

    Returning None here costs the subtitles and nothing else, which is why a
    platform missing from the list went unnoticed for so long.
    """
    for candidate in font_candidates():
        if Path(candidate).exists():
            return candidate
    return None


def _escape_drawtext(text: str) -> str:
    """Neutralize what the *filtergraph* parser treats as syntax.

    ``%`` is deliberately left alone: the filter sets ``expansion=none``, so a
    percent sign is literal text, and escaping it to ``\\%`` is what made
    drawtext discard the cue instead.
    """
    out = text.replace("\\", "\\\\").replace("'", "’").replace(":", "\\:")
    return out.replace("\n", " ")
