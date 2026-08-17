"""Bearer-token auth for everything this process serves.

Adding MCP made this mandatory rather than optional. The service was designed to
be reached over loopback, and every route reflects that: `POST /agent/run` takes
an upload and spends model quota, `GET /projects` lists **all** projects, and
`/projects/{id}/assets/{path}` serves their renders and audio. Putting that on a
public address without a token hands strangers your quota and everyone else's
decks — so the token gates the whole app, not just `/mcp`.

Two rules, both aimed at the same foot-gun:

* A request without the token gets 401 — except ``/health``, which a load
  balancer needs before it can know anything.
* Binding to a non-loopback address with no token configured refuses to start.
  A service that quietly comes up wide open is worse than one that fails.
"""

from __future__ import annotations

import hmac
import ipaddress

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import JSONResponse

from ..core.errors import InvalidRequest
from ..core.logging import get_logger

log = get_logger(__name__)

# Reachable before authenticating: liveness only, no project data.
PUBLIC_PATHS = frozenset({"/health"})


class BearerTokenMiddleware(BaseHTTPMiddleware):
    def __init__(self, app, token: str) -> None:
        super().__init__(app)
        self._token = token

    async def dispatch(self, request: Request, call_next):
        if request.url.path in PUBLIC_PATHS:
            return await call_next(request)

        header = request.headers.get("authorization", "")
        scheme, _, presented = header.partition(" ")
        # compare_digest keeps a wrong token from being narrowed down by timing.
        if scheme.lower() != "bearer" or not hmac.compare_digest(presented, self._token):
            return JSONResponse(
                status_code=401,
                content={"code": "unauthorized", "message": "缺少或错误的 Bearer token"},
            )
        return await call_next(request)


def is_loopback(host: str) -> bool:
    if host in {"localhost", ""}:
        return True
    try:
        return ipaddress.ip_address(host).is_loopback
    except ValueError:
        # A hostname we cannot classify is not loopback as far as we know.
        return False


def check_exposure(host: str, token: str) -> None:
    """Refuse to serve a non-loopback address without a token."""
    if not is_loopback(host) and not token:
        raise InvalidRequest(
            f"拒绝在 {host} 上无鉴权启动：这会把上传、工程列表和成片暴露给任何人。"
            "请设置 D2V_API_TOKEN，或用 --host 127.0.0.1 只监听本机。",
            detail={"host": host},
        )
