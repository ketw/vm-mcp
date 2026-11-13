"""security.py — one pure-ASGI gate in front of everything.

  /health /ping /version     open
  /ui/...                    web dashboard (its own cookie login, see webui/app.py)
  everything else            needs the X-Api-Key header (the MCP SSE stream + message posts)

Also: IP allowlist, per-IP rate limit, and connect/disconnect tracking of the MCP SSE stream — so the
dashboard can show exactly when a client's connection dropped and why.

Pure ASGI on purpose: Starlette's BaseHTTPMiddleware wraps/streams responses through an extra task layer that
is known to misbehave with long-lived SSE connections.
"""

from __future__ import annotations

import asyncio
import hmac
import ipaddress
import threading
import time

from . import VERSION
from .activity import store
from .config import ALLOWED_NETWORKS, API_KEY, RATE_LIMIT
from .logs import log

_buckets: dict[str, list[float]] = {}
_lock = threading.Lock()
_last_reject: dict[str, float] = {}


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


async def _reply(send, status: int, body: bytes, ctype: str = "application/json") -> None:
    await send({"type": "http.response.start", "status": status,
                "headers": [(b"content-type", ctype.encode()), (b"content-length", str(len(body)).encode())]})
    await send({"type": "http.response.body", "body": body})


def _reject_note(ip: str, why: str) -> None:
    """Record a rejected request in the dashboard, at most once per 30 s per (ip, reason)."""
    now = time.monotonic()
    if now - _last_reject.get(f"{ip}|{why}", 0) > 30:
        _last_reject[f"{ip}|{why}"] = now
        store.note("connection", f"Rejected request from {ip}: {why}", event="reject", client=ip, status="error")


class Gate:
    def __init__(self, app, ui_app):
        self.app, self.ui = app, ui_app

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":                       # lifespan etc.
            return await self.app(scope, receive, send)

        path = scope["path"]
        ip = scope["client"][0] if scope.get("client") else "unknown"

        if path in ("/health", "/ping", "/version"):
            return await _reply(send, 200, f"vm-mcp {VERSION} ok".encode(), "text/plain")

        if ALLOWED_NETWORKS:
            try:
                addr = ipaddress.ip_address(ip)
                allowed = any(addr in n for n in ALLOWED_NETWORKS)
            except ValueError:
                allowed = False
            if not allowed:
                log.warning("Blocked IP: %s", ip)
                _reject_note(ip, "IP not in allowlist")
                return await _reply(send, 403, b'{"error":"IP not in allowlist"}')

        if not _rate_ok(ip):
            log.warning("Rate limit hit: %s", ip)
            _reject_note(ip, "rate limit exceeded")
            return await _reply(send, 429, b'{"error":"Rate limit exceeded"}')

        if path == "/ui" or path.startswith("/ui/"):
            return await self.ui(scope, receive, send)

        headers = {k.decode("latin-1").lower(): v.decode("latin-1") for k, v in scope["headers"]}
        if not hmac.compare_digest(headers.get("x-api-key", ""), API_KEY):
            log.warning("Bad API key from %s", ip)
            _reject_note(ip, "bad or missing X-Api-Key")
            return await _reply(send, 401, b'{"error":"Unauthorized"}')

        if scope["method"] == "GET" and path.rstrip("/").endswith("/sse"):
            return await self._tracked_stream(scope, receive, send, ip)
        return await self.app(scope, receive, send)

    async def _tracked_stream(self, scope, receive, send, ip: str):
        t0, reason = time.monotonic(), "client closed"
        store.connection_opened(ip)
        log.info("MCP stream opened by %s", ip)
        try:
            await self.app(scope, receive, send)
        except asyncio.CancelledError:
            reason = "stream cancelled (client dropped or server shutting down)"
            raise
        except BaseException as exc:
            reason = f"error: {type(exc).__name__}: {exc}"
            raise
        finally:
            dur = time.monotonic() - t0
            log.info("MCP stream closed (%s) after %.0fs: %s", ip, dur, reason)
            store.connection_closed(ip, dur, reason)
