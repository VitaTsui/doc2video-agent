"""Typed errors. Every failure the pipeline can produce is one of these."""

from __future__ import annotations


class Doc2VideoError(Exception):
    """Base class for all domain errors."""

    code = "internal_error"
    http_status = 500

    def __init__(self, message: str, *, detail: dict | None = None) -> None:
        super().__init__(message)
        self.message = message
        self.detail = detail or {}

    def as_dict(self) -> dict:
        return {"code": self.code, "message": self.message, "detail": self.detail}


class ProjectNotFound(Doc2VideoError):
    code = "project_not_found"
    http_status = 404


class UnsupportedSource(Doc2VideoError):
    """Uploaded file type is not one of PDF / PPT / PPTX."""

    code = "unsupported_source"
    http_status = 400


class InvalidRequest(Doc2VideoError):
    code = "invalid_request"
    http_status = 400


class DependencyMissing(Doc2VideoError):
    """An optional external binary (ffmpeg, node, soffice...) is unavailable."""

    code = "dependency_missing"
    http_status = 503


class SkillFailed(Doc2VideoError):
    """A skill could not produce a usable result after its retries."""

    code = "skill_failed"


class TooBusy(Doc2VideoError):
    """Work was refused because the queue is full.

    503 rather than 429: this is not a rate limit on the caller, it is the
    service saying it has no capacity right now — a render occupies a CPU for
    minutes and there is only so much of one.
    """

    code = "too_busy"
    http_status = 503


class ToolFailed(Doc2VideoError):
    """A tool invocation failed (parser, TTS, renderer, ffmpeg...)."""

    code = "tool_failed"
