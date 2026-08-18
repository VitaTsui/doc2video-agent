"""Project inspection and script submission.

The 工程 is the asset, so it is fully readable. Writing is narrower: a caller
may replace the script — the one thing this service does not author for itself
— and everything downstream of that is recomputed rather than edited.
"""

from __future__ import annotations

from fastapi import APIRouter, HTTPException
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field

from ...agent import JobRequest
from ...core.errors import Doc2VideoError
from ..deps import get_agent, get_jobs


class NarrationsIn(BaseModel):
    """Page index (as a string, since JSON object keys are strings) -> script."""

    narrations: dict[str, str] = Field(default_factory=dict)


class SceneNarrationIn(BaseModel):
    narration: str

router = APIRouter(prefix="/projects", tags=["projects"])


@router.get("")
def list_projects() -> dict:
    return {"items": get_agent().list_projects()}


@router.get("/{project_id}")
def get_project(project_id: str) -> dict:
    return _load(project_id).model_dump(mode="json")


@router.get("/{project_id}/timeline")
def get_timeline(project_id: str) -> dict:
    return _load(project_id).timeline.model_dump(mode="json")


@router.get("/{project_id}/scenes")
def get_scenes(project_id: str) -> dict:
    project = _load(project_id)
    return {
        "items": [
            {
                "scene_id": scene.scene_id,
                "source_page": scene.source_page,
                "title": scene.title,
                "duration": scene.duration,
                "narration": scene.narration,
                "actions": [a.model_dump(mode="json") for a in scene.actions],
                "visual": scene.visual.model_dump(mode="json"),
                "audio": scene.audio.model_dump(mode="json"),
            }
            for scene in project.scenes
        ]
    }


@router.post("/{project_id}/narrations")
def submit_narrations(project_id: str, body: NarrationsIn) -> dict:
    """Voice, direct, render and check the project using this script.

    The same work ``render_video`` does over MCP. It exists over HTTP because
    the desktop app is a first-class client and should not have to speak MCP to
    a server inside its own process. Returns a job id; rendering takes minutes.
    """
    _load(project_id)  # 404 before a job is queued for a project that is not there
    job = get_jobs().submit(
        JobRequest(
            message="按调用方讲稿生成视频",
            project_id=project_id,
            narrations=_page_keys(body.narrations),
        )
    )
    return {"job_id": job.id, "status": job.status}


@router.post("/{project_id}/scenes/{scene_id}/narration")
def revise_scene(project_id: str, scene_id: str, body: SceneNarrationIn) -> dict:
    """Replace one scene's script and re-render only that scene."""
    project = _load(project_id)
    if project.scene(scene_id) is None:
        raise HTTPException(
            status_code=404, detail={"code": "scene_not_found", "message": "场景不存在"}
        )
    job = get_jobs().submit(
        JobRequest(
            message="按调用方讲稿修改场景",
            project_id=project_id,
            scene_narrations={scene_id: body.narration},
        )
    )
    return {"job_id": job.id, "status": job.status}


@router.get("/{project_id}/narration-guide")
def narration_guide(project_id: str) -> dict:
    """Per-page seconds and character budget to write the script against."""
    from ...skills import NarrationSkill
    from ...skills.base import SkillContext

    project = _load(project_id)
    return {"items": NarrationSkill(SkillContext.build(project)).guide()}


@router.get("/{project_id}/review")
def get_review(project_id: str) -> dict:
    return {"items": [f.model_dump(mode="json") for f in _load(project_id).review]}


@router.get("/{project_id}/quality")
def get_quality(project_id: str) -> dict:
    """The scored quality report, or 404 before the project has been reviewed."""
    quality = _load(project_id).quality
    if quality is None:
        raise HTTPException(
            status_code=404,
            detail={"code": "quality_not_ready", "message": "工程尚未质检"},
        )
    return quality.model_dump(mode="json")


@router.get("/{project_id}/ledger")
def get_ledger(project_id: str) -> dict:
    """How this project got made, step by step, with what each step produced.

    Separate from `/telemetry`, which answers an operator's questions (is it
    slow, did something quietly degrade). This answers the one the person
    watching a render actually has: what did that step make, and can I look at
    it? File-backed artifacts carry a project-relative path, servable through
    the same `/assets/` route the window already uses for slide thumbnails.
    """
    _load(project_id)
    return {"items": [e.model_dump(mode="json") for e in get_agent().read_ledger(project_id)]}


@router.get("/{project_id}/telemetry")
def get_telemetry(project_id: str) -> dict:
    """The last run's stage timings and degradations."""
    record = _load(project_id).telemetry
    if record is None:
        raise HTTPException(
            status_code=404,
            detail={"code": "telemetry_not_ready", "message": "工程尚未产生运行记录"},
        )
    return record.model_dump(mode="json")


@router.get("/{project_id}/video")
def get_video(project_id: str) -> FileResponse:
    agent = get_agent()
    try:
        path = agent.output_file(project_id)
    except Doc2VideoError as exc:
        raise HTTPException(status_code=exc.http_status, detail=exc.as_dict()) from exc
    if path is None or not path.exists():
        raise HTTPException(
            status_code=404,
            detail={"code": "output_not_ready", "message": "成片尚未生成"},
        )
    return FileResponse(path, media_type="video/mp4", filename=f"{project_id}.mp4")


@router.get("/{project_id}/assets/{asset_path:path}")
def get_asset(project_id: str, asset_path: str) -> FileResponse:
    """Serve a project asset (page renders, audio clips) for preview UIs."""
    agent = get_agent()
    base = agent.store.project_dir(project_id).resolve()
    target = (base / asset_path).resolve()
    # Path traversal guard: never serve anything outside the project directory.
    if not target.is_file() or base not in target.parents:
        raise HTTPException(
            status_code=404, detail={"code": "asset_not_found", "message": "资源不存在"}
        )
    return FileResponse(target)


@router.delete("/{project_id}")
def delete_project(project_id: str) -> dict:
    try:
        get_agent().delete_project(project_id)
    except Doc2VideoError as exc:
        raise HTTPException(status_code=exc.http_status, detail=exc.as_dict()) from exc
    return {"deleted": project_id}


def _page_keys(narrations: dict[str, str]) -> dict[int, str]:
    converted: dict[int, str] = {}
    for key, text in narrations.items():
        try:
            converted[int(key)] = text
        except (TypeError, ValueError):
            raise HTTPException(
                status_code=400,
                detail={"code": "invalid_request", "message": f"页码必须是整数，收到 {key!r}"},
            ) from None
    return converted


def _load(project_id: str):
    try:
        return get_agent().get_project(project_id)
    except Doc2VideoError as exc:
        raise HTTPException(status_code=exc.http_status, detail=exc.as_dict()) from exc
