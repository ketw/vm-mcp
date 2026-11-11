"""security.py — API-key auth, IP allowlist and per-IP rate limiting (ASGI middleware)."""

from __future__ import annotations

import ipaddress
import threading
import time

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import JSONResponse, PlainTextResponse

from . import VERSION
from .config import ALLOWED_NETWORKS, API_KEY, RATE_LIMIT
from .logs import log

_buckets: dict[str, list[float]] = {}
_lock = threading.Lock()


def _rate_ok(client_ip: str) -> bool:
    if RATE_LIMIT <= 0:
        return True
    now = time.monotonic()
    with _lock:
        stamps = [t for t in _buckets.get(client_ip, []) if now - t < 60.0]
        allowed = len(stamps) < RATE_LIMIT
        if allowed:
            stamps.append(now)
        _buckets[client_ip] = stamps
    return allowed


class AuthMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        path = request.url.path
        if path in ("/health", "/ping", "/version"):
            return PlainTextResponse(f"vm-mcp {VERSION} ok")

        client_ip = request.client.host if request.client else "unknown"

        if ALLOWED_NETWORKS:
            try:
                addr = ipaddress.ip_address(client_ip)
            except ValueError:
                return JSONResponse({"error": "Cannot parse client IP"}, status_code=403)
            if not any(addr in net for net in ALLOWED_NETWORKS):
                log.warning("Blocked IP: %s", client_ip)
                return JSONResponse({"error": "IP not in allowlist"}, status_code=403)

        if not _rate_ok(client_ip):
            log.warning("Rate limit hit: %s", client_ip)
            return JSONResponse({"error": "Rate limit exceeded"}, status_code=429)

        if request.headers.get("X-Api-Key", "") != API_KEY:
            log.warning("Bad API key from %s", client_ip)
            return JSONResponse({"error": "Unauthorized"}, status_code=401)

        log.info("%s %s", request.method, path)
        return await call_next(request)
