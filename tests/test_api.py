"""API surface: the agent entry point's contract, without running a pipeline."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from doc2video.api.app import create_app


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
