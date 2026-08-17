"""FastAPI application factory."""

from __future__ import annotations

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from ..core.config import get_settings
from ..core.errors import Doc2VideoError
from ..core.logging import setup_logging
from .routes import agent, health, jobs, projects

DESCRIPTION = """
PDF / PPT 智能讲解视频 Agent。

对外只有一个 Agent 入口：`POST /agent/run`。首次上传文件生成视频，之后带上
`project_id` 用自然语言继续修改；所有状态围绕 VideoProject，支持场景级增量重渲染。
"""


def create_app() -> FastAPI:
    settings = get_settings()
    setup_logging(settings.log_level)

    app = FastAPI(
        title="Doc2Video Agent",
        description=DESCRIPTION,
        version="0.1.0",
    )
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    app.include_router(health.router)
    app.include_router(agent.router)
    app.include_router(jobs.router)
    app.include_router(projects.router)

    @app.exception_handler(Doc2VideoError)
    async def domain_error_handler(_: Request, exc: Doc2VideoError) -> JSONResponse:
        return JSONResponse(status_code=exc.http_status, content=exc.as_dict())

    return app


app = create_app()
