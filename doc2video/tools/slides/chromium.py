"""Chromium slide renderer — the LibreOffice-free high-fidelity path.

Reuses the browser Remotion already ships with: the deck is handed to a
``Slides`` composition where **frame index = slide index**, and one
``remotion render --sequence`` produces every page image from a single bundle.
That keeps cost at roughly one browser start per deck instead of per slide.

Fidelity sits between the two existing backends: real fonts, theme colours,
gradients, rotation and z-order (unlike the Pillow rasterizer), but rendered by
a browser rather than by PowerPoint's own layout engine (unlike LibreOffice).
"""

from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

from ...core.config import which
from ...core.errors import ToolFailed
from ...core.logging import get_logger
from .model import SlideDeck

log = get_logger(__name__)

RENDERER_DIR = Path(__file__).resolve().parents[3] / "renderer"
COMPOSITION_ID = "Slides"
RENDER_TIMEOUT = 1800


class ChromiumSlideRenderer:
    name = "chromium"

    def __init__(self, renderer_dir: Path | None = None) -> None:
        self.renderer_dir = renderer_dir or RENDERER_DIR

    def available(self) -> bool:
        return (
            which("npx") is not None
            and (self.renderer_dir / "package.json").exists()
            and (self.renderer_dir / "node_modules").exists()
        )

    def unavailable_reason(self) -> str:
        if which("npx") is None:
            return "未安装 Node.js / npx"
        if not (self.renderer_dir / "node_modules").exists():
            return "Remotion 依赖未安装，请在 renderer/ 下执行 pnpm install"
        return "不可用"

    def render(self, deck: SlideDeck, assets_dir: Path) -> list[str]:
        """Render every slide to ``assets_dir``; returns project-relative paths."""
        if not deck.slides:
            return []

        staged_deck = self._stage(deck, assets_dir)
        work_dir = self.renderer_dir / "out" / "slides"
        if work_dir.exists():
            shutil.rmtree(work_dir)
        work_dir.mkdir(parents=True, exist_ok=True)

        props_path = work_dir / "deck.json"
        props_path.write_text(
            json.dumps(staged_deck.model_dump(mode="json"), ensure_ascii=False),
            encoding="utf-8",
        )

        cmd = [
            "npx", "remotion", "render",
            "src/index.ts", COMPOSITION_ID,
            str(work_dir.resolve()),
            f"--props={props_path.resolve()}",
            "--sequence",
            "--image-format=png",
            "--log=error",
        ]
        log.debug("chromium slides: %s", " ".join(cmd))
        try:
            subprocess.run(
                cmd,
                cwd=self.renderer_dir,
                check=True,
                capture_output=True,
                timeout=RENDER_TIMEOUT,
            )
        except subprocess.CalledProcessError as exc:
            raise ToolFailed(
                "Chromium 幻灯片渲染失败",
                detail={"stderr": exc.stderr.decode("utf-8", "ignore")[-1500:]},
            ) from exc
        except subprocess.TimeoutExpired as exc:
            raise ToolFailed("Chromium 幻灯片渲染超时") from exc

        return self._collect(work_dir, deck, assets_dir)

    # -- helpers -----------------------------------------------------------
    def _stage(self, deck: SlideDeck, assets_dir: Path) -> SlideDeck:
        """Copy embedded images where the browser can load them via staticFile."""
        staged = deck.model_copy(deep=True)
        public_dir = self.renderer_dir / "public" / "slides"
        public_dir.mkdir(parents=True, exist_ok=True)

        for slide in staged.slides:
            for shape in slide.shapes:
                if not shape.image:
                    continue
                source = assets_dir / shape.image
                if not source.exists():
                    shape.image = None
                    continue
                target = public_dir / shape.image
                shutil.copyfile(source, target)
                shape.image = f"slides/{shape.image}"
        return staged

    def _collect(self, work_dir: Path, deck: SlideDeck, assets_dir: Path) -> list[str]:
        """Move the rendered sequence into the project's assets directory."""
        frames = sorted(p for p in work_dir.glob("*.png"))
        if len(frames) < len(deck.slides):
            raise ToolFailed(
                "Chromium 渲染产出的页数不足",
                detail={"expected": len(deck.slides), "got": len(frames)},
            )

        paths: list[str] = []
        for slide, frame in zip(deck.slides, frames, strict=False):
            name = f"page_{slide.index:03d}.png"
            shutil.move(str(frame), assets_dir / name)
            paths.append(f"assets/{name}")
        shutil.rmtree(work_dir, ignore_errors=True)
        return paths
