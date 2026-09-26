"""Link-only access: anyone with the team link gets in, nobody else does. No accounts.

The link carries a secret: https://host/?k=<ACCESS_TOKEN>. On the first visit the server
checks it, stores a cookie derived from it (not the secret itself), and redirects to the same
address without `?k=`, so the secret doesn't sit in browser history or leak to the external
PDF sites the app links to. Every page and API call then needs that cookie (or the secret in
an `X-Access-Token` header, for scripts).

Rotating ACCESS_TOKEN revokes every old link and cookie at once; send the new link to
whoever should keep access. Setting ALLOW_OPEN_ACCESS=1 instead turns the check off (local
development only). With neither set, the app refuses to serve data rather than run open.
"""

from __future__ import annotations

import hashlib
import hmac
import os
from urllib.parse import urlencode

from fastapi import Request
from fastapi.responses import HTMLResponse, JSONResponse, PlainTextResponse, RedirectResponse, Response
from starlette.middleware.base import BaseHTTPMiddleware

COOKIE = "dbs_access"
QUERY_PARAM = "k"
HEADER = "x-access-token"
COOKIE_MAX_AGE = 60 * 60 * 24 * 180          # 180 days
OPEN_PATHS = {"/api/health", "/robots.txt"}   # health checks for the host; crawler opt-out

DENIED_PAGE = """<!doctype html><html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1"><meta name="robots" content="noindex">
<title>Private</title><style>body{font:16px/1.5 system-ui,sans-serif;max-width:32rem;margin:15vh auto;padding:0 16px;color:#1a1f29}
@media (prefers-color-scheme:dark){body{background:#11151c;color:#e6e9ef}}</style></head>
<body><h1>This app is private</h1><p>Open it with the team link. If your link stopped working, ask
for the current one: links are replaced from time to time.</p></body></html>"""


def access_token() -> str | None:
    return os.environ.get("ACCESS_TOKEN") or None


def open_access() -> bool:
    return os.environ.get("ALLOW_OPEN_ACCESS") == "1"


def cookie_value(token: str) -> str:
    """What the browser keeps: a digest of the secret, so the cookie alone never reveals it and
    changes when the secret is rotated."""
    return hmac.new(token.encode(), b"dow-budget-access-v1", hashlib.sha256).hexdigest()


def _secure_cookie(request: Request) -> bool:
    forced = os.environ.get("COOKIE_SECURE")
    if forced is not None:
        return forced == "1"
    proto = request.headers.get("x-forwarded-proto", request.url.scheme)
    return proto == "https"


def _denied(request: Request, status: int) -> Response:
    if request.url.path.startswith("/api/"):
        return JSONResponse({"detail": "This app is private. Open it with the team link."}, status_code=status)
    return HTMLResponse(DENIED_PAGE, status_code=status)


class LinkAccessMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next) -> Response:
        path = request.url.path
        if path == "/robots.txt":
            return self._headers(PlainTextResponse("User-agent: *\nDisallow: /\n"))
        if path in OPEN_PATHS or open_access():
            return self._headers(await call_next(request))

        token = access_token()
        if token is None:
            return self._headers(JSONResponse(
                {"detail": "ACCESS_TOKEN is not configured; refusing to serve data without it."}, status_code=503))

        offered = request.query_params.get(QUERY_PARAM)
        if offered is not None:
            if not hmac.compare_digest(offered.encode(), token.encode()):
                return self._headers(_denied(request, 404))
            rest = [(k, v) for k, v in request.query_params.multi_items() if k != QUERY_PARAM]
            target = path + (f"?{urlencode(rest)}" if rest else "")
            resp = RedirectResponse(target, status_code=303)
            resp.set_cookie(COOKIE, cookie_value(token), max_age=COOKIE_MAX_AGE, httponly=True,
                            secure=_secure_cookie(request), samesite="lax", path="/")
            return self._headers(resp)

        cookie = request.cookies.get(COOKIE, "")
        header = request.headers.get(HEADER, "")
        if hmac.compare_digest(cookie.encode(), cookie_value(token).encode()) or (
            header and hmac.compare_digest(header.encode(), token.encode())
        ):
            return self._headers(await call_next(request))
        return self._headers(_denied(request, 401))

    @staticmethod
    def _headers(resp: Response) -> Response:
        resp.headers["Referrer-Policy"] = "no-referrer"
        resp.headers["X-Robots-Tag"] = "noindex, nofollow"
        resp.headers["X-Content-Type-Options"] = "nosniff"
        resp.headers["X-Frame-Options"] = "DENY"
        return resp


def share_link(base_url: str, token: str) -> str:
    return f"{base_url.rstrip('/')}/?{urlencode({QUERY_PARAM: token})}"
