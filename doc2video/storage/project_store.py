"""Filesystem-backed VideoProject store.

One directory per project holds the project JSON and every artifact it points
at, so a project is self-contained: copy the directory and the video can be
re-rendered elsewhere. Paths inside the project JSON are always *relative* to
the project directory — absolute paths only appear at renderer boundaries.
"""

from __future__ import annotations

import json
import shutil
from pathlib import Path

from ..core.config import Settings, get_settings
from ..core.errors import ProjectNotFound
from ..core.logging import get_logger
from ..schemas import VideoProject

log = get_logger(__name__)

PROJECT_FILE = "project.json"
SUBDIRS = ("source", "assets", "audio", "clips", "out")


class ProjectStore:
    def __init__(self, settings: Settings | None = None) -> None:
        self._settings = settings or get_settings()
        self.root = self._settings.projects_dir
        self.root.mkdir(parents=True, exist_ok=True)

    # -- layout --------------------------------------------------------
    def project_dir(self, project_id: str) -> Path:
        return self.root / project_id

    def ensure_layout(self, project_id: str) -> Path:
        base = self.project_dir(project_id)
        for name in SUBDIRS:
            (base / name).mkdir(parents=True, exist_ok=True)
        return base

    def resolve(self, project_id: str, relative_path: str | None) -> Path | None:
        """Turn a project-relative path into an absolute one."""
        if not relative_path:
            return None
        return (self.project_dir(project_id) / relative_path).resolve()

    def relativize(self, project_id: str, path: Path) -> str:
        base = self.project_dir(project_id).resolve()
        try:
            return str(path.resolve().relative_to(base))
        except ValueError:
            # Outside the project directory: keep it absolute rather than lie.
            return str(path.resolve())

    # -- persistence ---------------------------------------------------
    def save(self, project: VideoProject) -> Path:
        project.touch()
        base = self.ensure_layout(project.project_id)
        target = base / PROJECT_FILE
        # Write-then-rename so a crash mid-write cannot corrupt the project.
        tmp = target.with_suffix(".json.tmp")
        tmp.write_text(project.model_dump_json(indent=2), encoding="utf-8")
        tmp.replace(target)
        return target

    def load(self, project_id: str) -> VideoProject:
        path = self.project_dir(project_id) / PROJECT_FILE
        if not path.exists():
            raise ProjectNotFound(f"工程不存在：{project_id}", detail={"project_id": project_id})
        return VideoProject.model_validate_json(path.read_text(encoding="utf-8"))

    def exists(self, project_id: str) -> bool:
        return (self.project_dir(project_id) / PROJECT_FILE).exists()

    def list_projects(self) -> list[dict]:
        items: list[dict] = []
        for child in sorted(self.root.iterdir()) if self.root.exists() else []:
            manifest = child / PROJECT_FILE
            if not manifest.is_file():
                continue
            try:
                data = json.loads(manifest.read_text(encoding="utf-8"))
            except json.JSONDecodeError:
                log.warning("跳过损坏的工程文件：%s", manifest)
                continue
            items.append(
                {
                    "project_id": data.get("project_id", child.name),
                    "status": data.get("status"),
                    "title": data.get("document", {}).get("title", ""),
                    "source": data.get("source", {}).get("file", ""),
                    "updated_at": data.get("updated_at"),
                    "duration": sum(s.get("duration", 0) for s in data.get("scenes", [])),
                    "output": data.get("render", {}).get("output_path"),
                }
            )
        items.sort(key=lambda item: item.get("updated_at") or "", reverse=True)
        return items

    def delete(self, project_id: str) -> None:
        base = self.project_dir(project_id)
        if not base.exists():
            raise ProjectNotFound(f"工程不存在：{project_id}", detail={"project_id": project_id})
        shutil.rmtree(base)

    # -- artifact helpers ----------------------------------------------
    def import_source(
        self, project_id: str, source_path: Path, *, filename: str | None = None
    ) -> str:
        base = self.ensure_layout(project_id)
        name = filename or source_path.name
        target = base / "source" / name
        shutil.copy2(source_path, target)
        return self.relativize(project_id, target)

    def assets_dir(self, project_id: str) -> Path:
        return self.ensure_layout(project_id) / "assets"

    def audio_dir(self, project_id: str) -> Path:
        return self.ensure_layout(project_id) / "audio"

    def clips_dir(self, project_id: str) -> Path:
        return self.ensure_layout(project_id) / "clips"

    def out_dir(self, project_id: str) -> Path:
        return self.ensure_layout(project_id) / "out"
