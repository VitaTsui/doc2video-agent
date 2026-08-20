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
import re

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import JSONResponse

from ..core.errors import InvalidRequest
from ..core.logging import get_logger

log = get_logger(__name__)

# Reachable before authenticating: liveness only, no project data.
PUBLIC_PATHS = frozenset({"/health"})

# Routes a media element loads directly. ``<video src>`` and ``<img src>``
# cannot carry an Authorization header, so for these — and only these, and only
# for GET — the token may ride in the query string instead. Everything else
# still requires the header, so a leaked URL cannot start a render or read a
# project's JSON.
MEDIA_PATH = re.compile(r"^/(projects/[^/]+/(video|assets/.+)|health/voices/preview)$")


class BearerTokenMiddleware(BaseHTTPMiddleware):
    def __init__(self, app, token: str) -> None:
        super().__init__(app)
        self._token = token

    async def dispatch(self, request: Request, call_next):
        if request.url.path in PUBLIC_PATHS:
            return await call_next(request)

        # A CORS preflight carries no credentials — by specification, the
        # browser strips them — so rejecting it for having no token rejects
        # every cross-origin request the app ever makes, before it is made.
        # This middleware runs outside the CORS one (add_middleware prepends),
        # so nothing else would get the chance to answer. The response reveals
        # only which methods and headers are allowed.
        if request.method == "OPTIONS":
            return await call_next(request)

        header = request.headers.get("authorization", "")
        scheme, _, presented = header.partition(" ")
        # `<video src>` cannot send a header, and neither can a file input
        # posting through a component that only lets us configure a URL. Both
        # are same-origin calls from our own window to a loopback port, and
        # both would otherwise need the token woven through a library we do not
        # control. The query token is the same secret, in the only place these
        # two can carry it.
        if not presented and (
            (request.method == "GET" and MEDIA_PATH.match(request.url.path))
            or (request.method == "POST" and request.url.path == "/uploads")
        ):
            scheme, presented = "bearer", request.query_params.get("token", "")
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
