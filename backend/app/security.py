"""Request gatekeeping that runs before any route:

1. Host allowlist (blocks DNS rebinding). Only PUBLIC_HOST is served; localhost is accepted for
   /api/health alone, so the container healthcheck and deploy.sh can reach it.
2. Every non-GET request needs Content-Type: application/json and an Origin that exactly matches
   https://PUBLIC_HOST. No CORS headers are ever sent, and OPTIONS is refused.
3. /api/* needs a valid session, except /api/health and /api/auth/*.
4. Security headers on every response.
"""
from __future__ import annotations

import os
import time
from collections import defaultdict, deque

from fastapi import Request
from fastapi.responses import JSONResponse, Response

from . import sessions
from .config import settings

SAFE = {"GET", "HEAD"}
OPEN_API = ("/api/health", "/api/auth/")

CSP = "; ".join([
    "default-src 'self'",
    "script-src 'self'",
    "style-src 'self'",
    "img-src 'self' data:",
    "font-src 'self'",
    "connect-src 'self'",
    "manifest-src 'self'",
    "worker-src 'self'",
    "object-src 'none'",
    "base-uri 'none'",
    "form-action 'self'",
    "frame-ancestors 'none'",
])
HEADERS = {
    "Content-Security-Policy": CSP,
    "X-Content-Type-Options": "nosniff",
    "Referrer-Policy": "no-referrer",
    "X-Frame-Options": "DENY",
    "Cross-Origin-Opener-Policy": "same-origin",
    "Cross-Origin-Resource-Policy": "same-origin",
    "Permissions-Policy": "camera=(), microphone=(), geolocation=(), payment=(), usb=()",
}


def allowed_hosts() -> set[str]:
    s = settings()
    extra = {h.strip().lower() for h in os.environ.get("ALLOWED_HOSTS", "").split(",") if h.strip()}
    return {s.public_host.lower()} | extra


def allowed_origins() -> set[str]:
    s = settings()
    return {s.origin} | set(s.dev_origins)


def _host(request: Request) -> str:
    h = request.headers.get("host", "").lower()
    if h.startswith("["):
        return h.split("]")[0] + "]"
    return h.rsplit(":", 1)[0] if ":" in h else h


def deny(status: int, msg: str) -> JSONResponse:
    return JSONResponse({"error": msg}, status_code=status)


async def gate(request: Request, call_next):
    path = request.url.path
    host = _host(request)
    if host not in allowed_hosts() and not (path == "/api/health" and host in settings().local_hosts):
        return deny(421, "unknown host")
    if request.method == "OPTIONS":
        return deny(405, "method not allowed")
    if request.method not in SAFE:
        if request.headers.get("origin") not in allowed_origins():
            return deny(403, "bad origin")
        ctype = request.headers.get("content-type", "").split(";")[0].strip().lower()
        if ctype != "application/json":
            return deny(415, "content type must be application/json")
    if path.startswith("/api/") and not path.startswith(OPEN_API):
        sess = sessions.current(request)
        if sess is None:
            return deny(401, "locked")
        request.state.session = sess
    response: Response = await call_next(request)
    for k, v in HEADERS.items():
        response.headers.setdefault(k, v)
    if path.startswith("/api/"):
        response.headers["Cache-Control"] = "no-store"
    if host == settings().public_host:
        response.headers["Strict-Transport-Security"] = "max-age=31536000"
    return response


class RateLimit:
    """Sliding-window limiter, in memory (one process, one user)."""

    def __init__(self, limit: int, window: float = 60):
        self.limit, self.window = limit, window
        self.hits: dict[str, deque[float]] = defaultdict(deque)

    def check(self, key: str) -> bool:
        now = time.monotonic()
        q = self.hits[key]
        while q and now - q[0] > self.window:
            q.popleft()
        if len(q) >= self.limit:
            return False
        q.append(now)
        return True

    def reset(self) -> None:
        self.hits.clear()


def client_ip(request: Request) -> str:
    return request.client.host if request.client else "?"
