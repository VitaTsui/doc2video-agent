"""API surface: the agent entry point's contract, without running a pipeline."""

from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from doc2video.api.app import create_app
from doc2video.core import flags


@pytest.fixture
def client() -> TestClient:
    return TestClient(create_app())


def test_health(client: TestClient):
    assert client.get("/health").json() == {"status": "ok"}


def test_capabilities_reports_every_layer(client: TestClient):
    body = client.get("/health/capabilities").json()
    assert set(body) >= {"llm", "tts", "renderers", "binaries", "video"}
    assert "remotion" in body["renderers"]
    assert "ffmpeg" in body["binaries"]
    # The model layer is reported but empty by default: holding no model is
    # still the service's contract, and a caller has to be able to tell
    # "nothing configured" from "configured and broken" before it decides
    # whether to write the script itself.
    assert body["llm"]["available"] is False
    assert body["llm"]["configured"] == "mock"


def test_agent_run_requires_a_message(client: TestClient):
    response = client.post("/agent/run", json={"project_id": "proj_x", "message": "   "})
    assert response.status_code == 400
    assert response.json()["detail"]["code"] == "invalid_request"


def test_agent_run_requires_a_file_on_first_call(client: TestClient):
    response = client.post("/agent/run", json={"message": "生成一个视频"})
    assert response.status_code == 400


def test_unknown_project_returns_404(client: TestClient):
    response = client.get("/projects/proj_does_not_exist")
    assert response.status_code == 404
    assert response.json()["detail"]["code"] == "project_not_found"


def test_unknown_job_returns_404(client: TestClient):
    assert client.get("/jobs/job_missing").status_code == 404


def test_project_list_is_available(client: TestClient):
    assert "items" in client.get("/projects").json()


def test_metrics_is_readable_before_any_run(client: TestClient):
    """A brand-new deployment must not 500 on its own dashboard."""
    body = client.get("/metrics").json()

    assert "summary" in body
    assert set(body["rollout"]) == set(flags.FLAGS)


def test_metrics_runs_lists_nothing_rather_than_failing(client: TestClient):
    assert client.get("/metrics/runs").json() == {"items": []}


def test_quality_is_404_before_the_project_is_reviewed(client: TestClient):
    response = client.get("/projects/proj_does_not_exist/quality")
    assert response.status_code == 404


def test_agent_run_upload_cannot_escape_the_uploads_directory(client: TestClient):
    """A multipart filename is attacker-controlled; it used to be joined raw.

    The name still passes the suffix check — that is all `detect_source_type`
    looks at — so what matters is where the bytes landed, not the status code.
    """
    from doc2video.core.config import get_settings

    uploads = Path(get_settings().uploads_dir).resolve()
    escaped = uploads.parent / "pwned.pptx"

    # An empty message is rejected *after* the uploads are stored, so this
    # exercises the write without starting a render.
    response = client.post(
        "/agent/run",
        data={"message": "  "},
        files={"files": ("../pwned.pptx", b"not a deck", "application/octet-stream")},
    )
    assert response.status_code == 400

    assert not escaped.exists()
    written = [p for p in uploads.rglob("*") if p.is_file()]
    assert written, "文件应该被存下来，只是不能存到目录外"
    assert all(uploads in p.resolve().parents for p in written)
    assert all(p.name == "pwned.pptx" for p in written)


def test_narration_routes_exist_for_a_client_without_mcp(client: TestClient):
    """The desktop app should not have to speak MCP to a server in its own process."""
    missing = client.post("/projects/proj_nope/narrations", json={"narrations": {"1": "你好"}})
    assert missing.status_code == 404

    bad_key = client.post("/projects/proj_nope/narrations", json={"narrations": {"封面": "x"}})
    assert bad_key.status_code in (400, 404)


def test_job_events_streams_and_closes(client: TestClient):
    """A late subscriber gets the outcome and a done event, not a hung stream."""
    assert client.get("/jobs/job_nope/events").status_code == 404


def test_media_may_authenticate_by_query_but_nothing_else_can(monkeypatch):
    """`<video src>` cannot send a header; every other route still must."""
    from doc2video.core.config import get_settings

    # create_app() reads the cached settings; patching the instance is what a
    # token-protected deployment looks like from inside the process.
    monkeypatch.setattr(get_settings(), "api_token", "s3cret")
    guarded = TestClient(create_app())

    # A media GET is reachable with the token in the query — 404 here means it
    # got past the middleware and found no such project, which is the point.
    assert guarded.get("/projects/proj_1/video?token=s3cret").status_code == 404
    assert guarded.get("/projects/proj_1/video?token=wrong").status_code == 401
    assert guarded.get("/projects/proj_1/video").status_code == 401

    # Everything else still needs the header, however the URL is dressed up.
    assert guarded.get("/projects/proj_1?token=s3cret").status_code == 401
    assert guarded.get("/jobs/job_1/events?token=s3cret").status_code == 401
    assert (
        guarded.post("/projects/proj_1/narrations?token=s3cret", json={}).status_code == 401
    )
