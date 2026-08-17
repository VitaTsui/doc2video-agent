"""Project inspection: the工程 is the asset, so it is fully readable."""

from __future__ import annotations

from fastapi import APIRouter, HTTPException
from fastapi.responses import FileResponse

from ...core.errors import Doc2VideoError
from ..deps import get_agent

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


@router.get("/{project_id}/telemetry")
def get_telemetry(project_id: str) -> dict:
    """The last run's timings, model calls and cost."""
    record = _load(project_id).telemetry
    if record is None:
        raise HTTPException(
            status_code=404,
            detail={"code": "telemetry_not_ready", "message": "工程尚未产生运行记录"},
        )
    return {
        **record.model_dump(mode="json"),
        "cost_usd_total": record.cost_usd(),
        "cost_by_stage": record.cost_by_stage(),
        "total_tokens": record.total_tokens(),
    }


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


def _load(project_id: str):
    try:
        return get_agent().get_project(project_id)
    except Doc2VideoError as exc:
        raise HTTPException(status_code=exc.http_status, detail=exc.as_dict()) from exc
