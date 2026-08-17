"""Source-file upload, because an MCP client is not on this filesystem.

Over stdio a tool could take a path. Over HTTP the caller is somewhere else, so
the file has to arrive first and be referred to by id afterwards. This is a
plain multipart POST *and* an MCP tool: a big deck should travel as bytes, but
a client with no way out to HTTP would otherwise be locked out of step one.
Both paths land in ``store_upload`` so the size cap and the "is this even a
deck" check cannot drift apart.
"""

from __future__ import annotations

import shutil
import uuid
from pathlib import Path
from typing import BinaryIO

from fastapi import APIRouter, HTTPException, UploadFile

from ...core.config import Settings, get_settings
from ...core.errors import Doc2VideoError, InvalidRequest, UnsupportedSource
from ...core.logging import get_logger
from ...tools.parsers import detect_source_type

router = APIRouter(tags=["uploads"])
log = get_logger(__name__)


@router.post("/uploads")
async def upload(file: UploadFile) -> dict:
    """Store a PDF / PPT / PPTX and return the id the MCP tools expect."""
    name = Path(file.filename or "").name
    if not name:
        raise HTTPException(
            status_code=400, detail={"code": "invalid_request", "message": "缺少文件名"}
        )
    try:
        return store_upload(name, file.file)
    except Doc2VideoError as exc:
        raise HTTPException(status_code=exc.http_status, detail=exc.as_dict()) from exc


def store_upload(filename: str, source: BinaryIO) -> dict:
    """Persist one source file and return its id.

    Shared by the HTTP route and the MCP ``upload_source`` tool.
    """
    settings = get_settings()
    name = Path(filename).name
    if not name:
        raise InvalidRequest("缺少文件名")

    upload_id = f"up_{uuid.uuid4().hex[:12]}"
    # One directory per upload keeps the original filename (the parser
    # dispatches on its suffix) without letting two uploads collide on it.
    target_dir = settings.uploads_dir / upload_id
    target_dir.mkdir(parents=True, exist_ok=True)
    target = target_dir / name

    limit = settings.max_upload_mb * 1024 * 1024
    written = 0
    try:
        with target.open("wb") as handle:
            while chunk := source.read(1024 * 1024):
                written += len(chunk)
                if written > limit:
                    raise InvalidRequest(
                        f"文件超过 {settings.max_upload_mb}MB 上限",
                        detail={"limit_mb": settings.max_upload_mb},
                    )
                handle.write(chunk)
        # Reject a file the pipeline cannot open here rather than three stages
        # into a job, where finding out costs a render slot.
        detect_source_type(target)
    except Exception as exc:
        shutil.rmtree(target_dir, ignore_errors=True)
        if isinstance(exc, Doc2VideoError):
            raise
        raise UnsupportedSource(str(exc)) from exc

    log.info("收到上传 %s（%s，%.1fMB）", upload_id, name, written / 1024 / 1024)
    return {"upload_id": upload_id, "filename": name, "size_bytes": written}


def resolve_upload(settings: Settings | None, upload_id: str) -> Path:
    """The stored file for an upload id.

    ``upload_id`` arrives from a model, so it is treated as untrusted input:
    the resolved directory has to sit inside the uploads root, which rules out
    ``..`` and absolute paths.
    """
    settings = settings or get_settings()
    root = settings.uploads_dir.resolve()
    target_dir = (root / upload_id).resolve()
    if target_dir != root and root not in target_dir.parents:
        raise InvalidRequest("非法的 upload_id", detail={"upload_id": upload_id})

    files = sorted(p for p in target_dir.glob("*") if p.is_file()) if target_dir.is_dir() else []
    if not files:
        raise InvalidRequest("找不到这个上传文件", detail={"upload_id": upload_id})
    return files[0]
