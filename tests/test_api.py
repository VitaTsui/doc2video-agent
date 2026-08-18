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
