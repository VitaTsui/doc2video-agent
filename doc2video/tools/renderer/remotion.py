"""Remotion renderer adapter.

The reference renderer: React compositions consume the scene plan verbatim, so
zoom / highlight / pointer / transition behave identically on every run. The
Python side only marshals the plan and shells out to the Remotion CLI.
"""

from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

from ...core import ledger, programs
from ...core.config import Settings, get_settings, which
from ...core.errors import ToolFailed
from ...core.logging import get_logger
from .base import RendererAdapter, ScenePlan

log = get_logger(__name__)

COMPOSITION_ID = "Scene"
RENDER_TIMEOUT = 3600


class RemotionAdapter(RendererAdapter):
    name = "remotion"

    def __init__(self, settings: Settings | None = None) -> None:
        settings = settings or get_settings()
        # Read-only: the Remotion project. Writable: everything this render
        # produces on the way to the clip.
        self.renderer_dir = settings.node_dir
        self.public_dir = settings.render_work_dir / "public"

    def available(self) -> bool:
        return (
            which("npx") is not None
            and (self.renderer_dir / "package.json").exists()
            and (self.renderer_dir / "node_modules").exists()
        )

    def unavailable_reason(self) -> str:
        if which("npx") is None:
            return "未安装 Node.js / npx"
        if not (self.renderer_dir / "package.json").exists():
            return f"未找到 Remotion 工程：{self.renderer_dir}"
        return f"Remotion 依赖未安装，请在 {self.renderer_dir} 下执行 pnpm install"

    def render_scene(self, plan: ScenePlan, out_path: Path) -> Path:
        # Recorded per scene: a thirty-scene render is thirty calls, and which
        # one took nine seconds is the question someone actually asks.
        out_path.parent.mkdir(parents=True, exist_ok=True)
        staged = self._stage_assets(plan)
        props_path = out_path.with_suffix(".props.json")
        props_path.write_text(
            json.dumps(staged.model_dump(mode="json"), ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

        cmd = [
            # The resolved path: on Windows `npx` is `npx.cmd`, which
            # `CreateProcess` will not run by its bare name. See core.programs.
            programs.require("npx", "未安装 Node.js / npx"),
            "remotion",
            "render",
            "src/index.ts",
            COMPOSITION_ID,
            str(out_path.resolve()),
            f"--props={props_path.resolve()}",
            # Assets are staged outside the Remotion project so nothing is
            # written into it — an installed app's program directory is
            # read-only, and a render that needs to scribble there cannot run.
            f"--public-dir={self.public_dir.resolve()}",
            "--log=error",
        ]
        log.debug("remotion: %s", " ".join(cmd))
        try:
            with ledger.call(f"renderer:{self.name}", plan.scene_id):
                subprocess.run(
                    cmd,
                    cwd=self.renderer_dir,
                    check=True,
                    capture_output=True,
                    timeout=RENDER_TIMEOUT,
                )
        except subprocess.CalledProcessError as exc:
            raise ToolFailed(
                "Remotion 渲染失败",
                detail={
                    "scene_id": plan.scene_id,
                    "stderr": exc.stderr.decode("utf-8", "ignore")[-1500:],
                },
            ) from exc
        except subprocess.TimeoutExpired as exc:
            raise ToolFailed("Remotion 渲染超时", detail={"scene_id": plan.scene_id}) from exc
        return out_path

    def _stage_assets(self, plan: ScenePlan) -> ScenePlan:
        """Copy assets into the public dir and rewrite paths for staticFile.

        Remotion resolves browser-loadable assets from ``public/``; project
        directories live elsewhere, so each scene's page image is staged under a
        content-addressed name (re-rendering the same scene reuses the file).
        """
        staged = plan.model_copy(deep=True)
        public_dir = self.public_dir / "staged"
        public_dir.mkdir(parents=True, exist_ok=True)

        if plan.image:
            source = Path(plan.image)
            target = public_dir / f"{plan.scene_id}{source.suffix or '.png'}"
            if not target.exists() or target.stat().st_mtime < source.stat().st_mtime:
                shutil.copyfile(source, target)
            staged.image = f"staged/{target.name}"

        # Narration is muxed by ffmpeg during final assembly; embedding it here
        # too would double the audio in the finished video.
        staged.audio = None
        return staged
