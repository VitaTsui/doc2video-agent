"""FastAPI application factory."""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from ..core import version as core_version
from ..core.config import get_settings
from ..core.errors import Doc2VideoError
from ..core.logging import get_logger, setup_logging
from .routes import agent, health, jobs, metrics, projects, uploads
from .security import BearerTokenMiddleware

log = get_logger(__name__)

DESCRIPTION = """
PDF / PPT 智能讲解视频 Agent。

对外只有一个 Agent 入口：`POST /agent/run`。首次上传文件生成视频，之后带上
`project_id` 用自然语言继续修改；所有状态围绕 VideoProject，支持场景级增量重渲染。

同一个 Agent 也以 MCP 工具的形式挂在 `/mcp`（Streamable HTTP），供模型直接驱动。
"""

# Built lazily and kept: ``session_manager`` only exists after
# ``streamable_http_app()`` has been called, and the lifespan below has to enter
# that same instance's manager — two calls to the factory would give two.
_MCP_SERVER = None


def create_app() -> FastAPI:
    settings = get_settings()
    setup_logging(settings.log_level)

    app = FastAPI(
        title="Doc2Video Agent",
        description=DESCRIPTION,
        version=core_version(),
        lifespan=_lifespan if settings.mcp_enabled else None,
    )
    # Same-origin by default: a token is only as safe as the origins allowed to
    # send it from a browser. Widen deliberately, per deployment.
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origins,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )
    if settings.api_token:
        app.add_middleware(BearerTokenMiddleware, token=settings.api_token)
    else:
        log.warning("未设置 D2V_API_TOKEN：服务无鉴权，只应监听 127.0.0.1")

    app.include_router(health.router)
    app.include_router(agent.router)
    app.include_router(uploads.router)
    app.include_router(jobs.router)
    app.include_router(projects.router)
    app.include_router(metrics.router)

    if settings.mcp_enabled:
        # The sub-app's own path is "/" because the mount already supplies the
        # "/mcp" prefix — leaving its default would put the endpoint at
        # /mcp/mcp, which answers "Not Found" to a correctly configured client.
        app.mount("/mcp", _mcp_server().streamable_http_app(
            streamable_http_path="/",
            transport_security=_transport_security(settings),
        ))

    @app.exception_handler(Doc2VideoError)
    async def domain_error_handler(_: Request, exc: Doc2VideoError) -> JSONResponse:
        return JSONResponse(status_code=exc.http_status, content=exc.as_dict())

    return app


def _transport_security(settings):
    """DNS-rebinding protection, widened to the deployment's own hostname.

    The SDK checks the Host header and answers 421 to anything it does not
    recognise, which defaults to loopback only — behind a domain that rejects
    every request unless the domain is listed. Left empty the default stands,
    so a local run needs no configuration.
    """
    if not settings.mcp_allowed_hosts:
        return None

    from mcp.server.transport_security import TransportSecuritySettings

    return TransportSecuritySettings(
        allowed_hosts=list(settings.mcp_allowed_hosts),
        allowed_origins=list(settings.cors_origins) or list(settings.mcp_allowed_hosts),
    )


def _mcp_server():
    global _MCP_SERVER
    if _MCP_SERVER is None:
        from ..mcp_server import build_server

        _MCP_SERVER = build_server()
    return _MCP_SERVER


@asynccontextmanager
async def _lifespan(_app: FastAPI) -> AsyncIterator[None]:
    """Run the MCP session manager for the life of the process.

    A mounted sub-application's own lifespan never runs, so without this the
    first MCP request fails with "Task group is not initialized" — the mount
    looks correctly wired and simply does not work.
    """
    async with _mcp_server().session_manager.run():
        yield


app = create_app()
