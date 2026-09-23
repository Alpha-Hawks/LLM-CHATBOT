"""
HTTP transport security.

Pure ASGI middleware (not BaseHTTPMiddleware) so the Server-Sent-Events chat stream is passed
through untouched instead of being buffered.

Adds, to every response:
    X-Content-Type-Options: nosniff
    Referrer-Policy: no-referrer
    Content-Security-Policy: frame-ancestors ...   only the college portal may embed the assistant
    Cache-Control: no-store                        on API responses, so a browser or proxy never
                                                   keeps a student's attendance or marks
    Strict-Transport-Security                      in production, on HTTPS responses

With ENFORCE_HTTPS=true, plain-HTTP requests are redirected to HTTPS (health probes excepted so
container orchestrators can still probe over HTTP). Behind a TLS-terminating reverse proxy the
original scheme is read from X-Forwarded-Proto, so the proxy must set it and the app must not be
reachable except through the proxy.
"""

from typing import List, Tuple

from backend.app.core.config import settings

HSTS_VALUE = b"max-age=31536000; includeSubDomains"


def _frame_ancestors() -> str:
    """'self' plus every configured origin, e.g. the Anvaya portal that embeds the widget."""
    origins = [o for o in settings.ALLOWED_ORIGINS if o]
    return "frame-ancestors 'self' " + " ".join(origins)


def _request_is_https(scope) -> bool:
    forwarded = next(
        (value for name, value in scope.get("headers", []) if name == b"x-forwarded-proto"), b""
    ).decode("latin-1").split(",")[0].strip().lower()
    return forwarded == "https" or (not forwarded and scope.get("scheme") in ("https", "wss"))


def _https_url(scope) -> str:
    host = next((value for name, value in scope.get("headers", []) if name == b"host"), b"").decode("latin-1")
    query = scope.get("query_string", b"").decode("latin-1")
    raw_path = scope.get("raw_path")
    path = raw_path.decode("latin-1") if raw_path else scope["path"]
    return f"https://{host}{path}" + (f"?{query}" if query else "")


class SecurityHeadersMiddleware:
    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope.get("type") != "http":
            await self.app(scope, receive, send)
            return

        path = scope.get("path", "")
        is_api = path.startswith(settings.API_V1_STR)
        is_probe = path.startswith(f"{settings.API_V1_STR}/health") or path.startswith("/health")
        https = _request_is_https(scope)

        if settings.ENFORCE_HTTPS and not https and not is_probe:
            location = _https_url(scope)
            await send({
                "type": "http.response.start",
                "status": 308,
                "headers": [(b"location", location.encode("latin-1")), (b"content-length", b"0")],
            })
            await send({"type": "http.response.body", "body": b""})
            return

        async def send_with_headers(message):
            if message.get("type") == "http.response.start":
                try:
                    headers: List[Tuple[bytes, bytes]] = list(message.get("headers", []))
                    present = {name.lower() for name, _ in headers}

                    def add(name: bytes, value: bytes):
                        if name not in present:
                            headers.append((name, value))

                    add(b"x-content-type-options", b"nosniff")
                    add(b"referrer-policy", b"no-referrer")
                    try:
                        add(b"content-security-policy", _frame_ancestors().encode("latin-1"))
                    except Exception:
                        pass
                    if is_api:
                        add(b"cache-control", b"no-store")
                    if https and settings.ENVIRONMENT != "development":
                        add(b"strict-transport-security", HSTS_VALUE)
                    message = {**message, "headers": headers}
                except Exception:
                    pass
            await send(message)

        await self.app(scope, receive, send_with_headers)
