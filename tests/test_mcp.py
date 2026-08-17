"""The MCP surface, driven by a real MCP client over the mounted ASGI app.

Not by calling the tool functions directly: half of what can break here is the
wiring rather than the logic — the mount path, the session manager's lifespan,
the schemas the SDK derives from the signatures. A client that can `initialize`
and `list_tools` proves the parts a unit test would skip.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path

import httpx2
import pytest
from mcp import ClientSession
from mcp.client.streamable_http import streamable_http_client

from doc2video.api.app import create_app
from doc2video.core.config import Settings, get_settings

pytestmark = pytest.mark.anyio

MCP_URL = "http://mcp.test/mcp/"


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


@pytest.fixture
def app(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """A fresh app over an isolated store, with auth on."""
    monkeypatch.setenv("D2V_STORAGE_DIR", str(tmp_path / "storage"))
    monkeypatch.setenv("D2V_API_TOKEN", "test-token")
    monkeypatch.setenv("D2V_LLM_PROVIDER", "mock")
    monkeypatch.setenv("D2V_TTS_PROVIDER", "mock")
    monkeypatch.setenv("D2V_MCP_ALLOWED_HOSTS", '["mcp.test"]')
    get_settings.cache_clear()
    # The API singletons are cached per process; a stale one would point at a
    # previous test's store.
    from doc2video.api import deps

    deps.get_agent.cache_clear()
    deps.get_jobs.cache_clear()
    yield create_app()
    get_settings.cache_clear()
    deps.get_agent.cache_clear()
    deps.get_jobs.cache_clear()


@asynccontextmanager
async def _session(app, token: str = "test-token") -> AsyncIterator[ClientSession]:
    """An MCP session spoken straight into the ASGI app — no socket needed."""
    headers = {"Authorization": f"Bearer {token}"} if token else {}
    async with httpx2.AsyncClient(
        transport=httpx2.ASGITransport(app=app), base_url="http://mcp.test", headers=headers
    ) as http_client:
        async with app.router.lifespan_context(app):
            async with streamable_http_client(MCP_URL, http_client=http_client) as (read, write):
                async with ClientSession(read, write) as session:
                    await session.initialize()
                    yield session


async def test_the_server_introduces_itself(app):
    async with httpx2.AsyncClient(
        transport=httpx2.ASGITransport(app=app),
        base_url="http://mcp.test",
        headers={"Authorization": "Bearer test-token"},
    ) as client:
        async with app.router.lifespan_context(app):
            async with streamable_http_client(MCP_URL, http_client=client) as (read, write):
                async with ClientSession(read, write) as session:
                    info = await session.initialize()

    assert info.server_info.name == "doc2video"


async def test_every_tool_is_exposed_with_a_description(app):
    async with _session(app) as session:
        tools = {t.name: t for t in (await session.list_tools()).tools}

    assert set(tools) == {
        "upload_source",
        "prepare_project",
        "render_video",
        "revise_scenes",
        "job_status",
        "project_summary",
        "list_projects",
        "video_download_path",
    }
    # A tool a model cannot understand is a tool it will misuse.
    assert all(t.description for t in tools.values())


async def test_prepare_asks_for_an_upload_id_not_a_path(app):
    """The caller's filesystem is not this machine's — the schema has to say so."""
    async with _session(app) as session:
        tools = {t.name: t for t in (await session.list_tools()).tools}

    assert set(tools["prepare_project"].input_schema["properties"]) == {"brief", "upload_id"}
    assert "upload_id" in (tools["prepare_project"].description or "")


async def test_render_takes_the_callers_script(app):
    """The service writes no script — the tool has to ask for one."""
    async with _session(app) as session:
        tools = {t.name: t for t in (await session.list_tools()).tools}

    assert set(tools["render_video"].input_schema["properties"]) == {
        "project_id",
        "narrations",
    }


async def test_a_tool_call_reaches_the_agent(app):
    async with _session(app) as session:
        result = await session.call_tool("list_projects", {})

    assert not result.is_error


async def test_an_unknown_upload_is_an_error_the_model_can_read(app):
    async with _session(app) as session:
        result = await session.call_tool(
            "prepare_project", {"brief": "生成一个 3 分钟的视频", "upload_id": "up_nope"}
        )

    assert result.is_error
    assert "upload" in result.content[0].text.lower() or "上传" in result.content[0].text


async def test_an_upload_id_cannot_escape_the_uploads_directory(app):
    """upload_id comes from a model, so it is untrusted input."""
    async with _session(app) as session:
        result = await session.call_tool(
            "prepare_project", {"brief": "x", "upload_id": "../../../etc"}
        )

    assert result.is_error


# -- auth ------------------------------------------------------------------


async def test_mcp_refuses_an_unauthenticated_client(app):
    async with httpx2.AsyncClient(
        transport=httpx2.ASGITransport(app=app), base_url="http://mcp.test"
    ) as client:
        response = await client.post(MCP_URL, json={"jsonrpc": "2.0", "id": 1, "method": "ping"})

    assert response.status_code == 401


async def test_the_rest_of_the_api_is_behind_the_same_token(app):
    """MCP auth alone would leave /projects and /agent/run wide open."""
    async with httpx2.AsyncClient(
        transport=httpx2.ASGITransport(app=app), base_url="http://mcp.test"
    ) as client:
        unauthorized = await client.get("/projects")
        authorized = await client.get(
            "/projects", headers={"Authorization": "Bearer test-token"}
        )

    assert unauthorized.status_code == 401
    assert authorized.status_code == 200


async def test_health_stays_reachable_for_probes(app):
    async with httpx2.AsyncClient(
        transport=httpx2.ASGITransport(app=app), base_url="http://mcp.test"
    ) as client:
        response = await client.get("/health")

    assert response.status_code == 200


async def test_a_wrong_token_is_rejected(app):
    async with httpx2.AsyncClient(
        transport=httpx2.ASGITransport(app=app), base_url="http://mcp.test"
    ) as client:
        response = await client.get("/projects", headers={"Authorization": "Bearer nope"})

    assert response.status_code == 401


# -- uploads ---------------------------------------------------------------


async def test_upload_then_create(app, demo_pptx: Path):
    """The two-step an HTTP MCP client actually performs."""
    async with httpx2.AsyncClient(
        transport=httpx2.ASGITransport(app=app),
        base_url="http://mcp.test",
        headers={"Authorization": "Bearer test-token"},
    ) as client:
        with demo_pptx.open("rb") as handle:
            uploaded = await client.post(
                "/uploads",
                files={"file": (demo_pptx.name, handle.read(), "application/vnd.ms-powerpoint")},
            )

    assert uploaded.status_code == 200
    upload_id = uploaded.json()["upload_id"]

    from doc2video.api.routes.uploads import resolve_upload

    resolved = resolve_upload(Settings(storage_dir=Path(get_settings().storage_dir)), upload_id)
    assert resolved.exists()
    assert resolved.name == demo_pptx.name


async def test_an_unsupported_file_is_rejected_on_upload(app, tmp_path: Path):
    """Better here than three stages into a job."""
    junk = tmp_path / "notes.txt"
    junk.write_text("这不是幻灯片", encoding="utf-8")

    async with httpx2.AsyncClient(
        transport=httpx2.ASGITransport(app=app),
        base_url="http://mcp.test",
        headers={"Authorization": "Bearer test-token"},
    ) as client:
        response = await client.post(
            "/uploads", files={"file": (junk.name, junk.read_bytes(), "text/plain")}
        )

    assert response.status_code == 400


# -- upload via MCP --------------------------------------------------------


async def test_a_deck_can_arrive_through_mcp_alone(app, demo_pptx: Path):
    """A client with no way out to HTTP must still get past step one."""
    import base64
    import json

    encoded = base64.b64encode(demo_pptx.read_bytes()).decode("ascii")

    async with _session(app) as session:
        result = await session.call_tool(
            "upload_source", {"filename": "demo.pptx", "content_base64": encoded}
        )

    assert not result.is_error
    assert json.loads(result.content[0].text)["upload_id"].startswith("up_")


async def test_bad_base64_is_rejected_before_touching_the_disk(app):
    async with _session(app) as session:
        result = await session.call_tool(
            "upload_source", {"filename": "demo.pptx", "content_base64": "这不是base64"}
        )

    assert result.is_error
