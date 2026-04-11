"""CSRF protection middleware — verifies Origin header on state-changing requests.

Cookie-based auth is vulnerable to CSRF. This middleware rejects POST/PUT/DELETE/PATCH
requests whose Origin doesn't match the server, unless the request uses a Bearer token
(which is inherently CSRF-safe since the browser doesn't attach it automatically).

Uses a pure ASGI implementation to avoid BaseHTTPMiddleware issues with WebSocket
and streaming responses (which break NiceGUI).
"""

import json
from urllib.parse import urlparse

_SAFE_METHODS = frozenset({b"GET", b"HEAD", b"OPTIONS"})
_SKIP_PREFIXES = ("/socket.io", "/_nicegui", "/auth/jwt/", "/auth/local", "/auth/ldap")


class CSRFMiddleware:
    def __init__(self, app, allowed_origins: list[str] | None = None):
        self.app = app
        self.allowed_origins = {o.rstrip("/").lower() for o in (allowed_origins or [])}

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        method = scope.get("method", "GET").encode()
        if method in _SAFE_METHODS:
            await self.app(scope, receive, send)
            return

        path = scope.get("path", "")
        if any(path.startswith(p) for p in _SKIP_PREFIXES):
            await self.app(scope, receive, send)
            return

        headers = dict(scope.get("headers", []))

        auth = headers.get(b"authorization", b"").decode()
        if auth.lower().startswith("bearer "):
            await self.app(scope, receive, send)
            return

        origin = headers.get(b"origin", b"").decode() or None
        if origin is None:
            referer = headers.get(b"referer", b"").decode() or None
            origin = _origin_from_referer(referer)

        if origin is None:
            await self.app(scope, receive, send)
            return

        origin_normalized = origin.rstrip("/").lower()
        expected = _expected_origin(headers, scope)

        if origin_normalized == expected or origin_normalized in self.allowed_origins:
            await self.app(scope, receive, send)
            return

        body = json.dumps({"detail": "CSRF check failed: origin mismatch"}).encode()
        await send({
            "type": "http.response.start",
            "status": 403,
            "headers": [
                [b"content-type", b"application/json"],
                [b"content-length", str(len(body)).encode()],
            ],
        })
        await send({"type": "http.response.body", "body": body})


def _origin_from_referer(referer: str | None) -> str | None:
    if not referer:
        return None
    parsed = urlparse(referer)
    if parsed.scheme and parsed.netloc:
        return f"{parsed.scheme}://{parsed.netloc}"
    return None


def _expected_origin(headers: dict[bytes, bytes], scope: dict) -> str:
    forwarded_proto = headers.get(b"x-forwarded-proto", b"").decode()
    scheme = forwarded_proto or scope.get("scheme", "http")
    host = headers.get(b"host", b"").decode() or f"{scope['server'][0]}:{scope['server'][1]}"
    return f"{scheme}://{host}".lower()
