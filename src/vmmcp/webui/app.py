"""webui/app.py — raw-ASGI dashboard backend. No framework, so it is trivially testable.

  GET  /ui/                 dashboard (or the login form)
  POST /ui/login            form field `key` = VM_MCP_API_KEY  -> sets an HttpOnly cookie
  GET  /ui/logout
  GET  /ui/api/state        versions, counters, agent + connection status
  GET  /ui/api/events       ?after=<id>   new events (+ recently-changed ones so running calls complete)
  GET  /ui/api/image/<id>   a stored screenshot
  GET  /ui/api/live         a fresh low-res screenshot straight from the desktop agent (never starts the agent)
  GET  /ui/api/logs         tail of the server log and the agent log
"""

from __future__ import annotations

import asyncio
import hashlib
import hmac
import json
import socket
import time
from http.cookies import SimpleCookie
from pathlib import Path
from urllib.parse import parse_qs

from .. import VERSION
from ..activity import store
from ..config import AGENT_LOG, API_KEY, LOG_FILE

_DIR = Path(__file__).resolve().parent
COOKIE = "vmmcp_ui"


def _cookie_value() -> str:
    return hmac.new(API_KEY.encode(), b"vm-mcp-dashboard", hashlib.sha256).hexdigest()[:48]


def _authed(headers: dict) -> bool:
    jar = SimpleCookie()
    try:
        jar.load(headers.get("cookie", ""))
    except Exception:
        return False
    morsel = jar.get(COOKIE)
    if morsel and hmac.compare_digest(morsel.value, _cookie_value()):
        return True
    return hmac.compare_digest(headers.get("x-api-key", ""), API_KEY) if API_KEY else False


async def _send(send, status: int, body: bytes, ctype: str, extra=()) -> None:
    await send({"type": "http.response.start", "status": status, "headers": [
        (b"content-type", ctype.encode()), (b"content-length", str(len(body)).encode()),
        (b"cache-control", b"no-store"), (b"x-content-type-options", b"nosniff"), *extra]})
    await send({"type": "http.response.body", "body": body})


def _json(obj) -> bytes:
    return json.dumps(obj, ensure_ascii=False, default=str).encode()


def _tail(path: Path, n: int) -> str:
    try:
        return "".join(path.read_text(encoding="utf-8", errors="replace").splitlines(True)[-n:])
    except OSError:
        return f"(no file at {path})"


async def _read_body(receive, limit: int = 8192) -> bytes:
    body = b""
    while True:
        msg = await receive()
        body += msg.get("body", b"")
        if not msg.get("more_body") or len(body) > limit:
            return body[:limit]


def _agent_status() -> dict:
    from ..computer import client                      # imported lazily: keeps the UI importable anywhere
    p = client._ping()
    return {"up": bool(p), **({k: p.get(k) for k in ("version", "session", "pid", "ui_scale", "screen")} if p else {})}


def _live_frame() -> bytes | None:
    from ..computer import client
    import base64
    if not client._ping():
        return None
    r = client._post("screenshot", {"out_width": 960, "format": "jpeg", "quality": 60, "label_scale": 1.0}, 8)
    return base64.b64decode(r["image_b64"]) if r.get("ok") else None


async def ui_app(scope, receive, send):
    if scope["type"] != "http":
        return
    path, method = scope["path"], scope["method"]
    sub = path[3:] or "/"
    headers = {k.decode("latin-1").lower(): v.decode("latin-1") for k, v in scope["headers"]}
    query = parse_qs(scope.get("query_string", b"").decode())

    if sub == "/login" and method == "POST":
        form = parse_qs((await _read_body(receive)).decode("utf-8", "replace"))
        if API_KEY and hmac.compare_digest(form.get("key", [""])[0], API_KEY):
            cookie = f"{COOKIE}={_cookie_value()}; Path=/ui; HttpOnly; SameSite=Strict; Max-Age=2592000"
            return await _send(send, 303, b"", "text/plain", [(b"location", b"/ui/"), (b"set-cookie", cookie.encode())])
        return await _send(send, 303, b"", "text/plain", [(b"location", b"/ui/?bad=1")])

    if sub == "/logout":
        return await _send(send, 303, b"", "text/plain", [(b"location", b"/ui/"),
                           (b"set-cookie", f"{COOKIE}=; Path=/ui; Max-Age=0".encode())])

    if not _authed(headers):
        if sub in ("/", ""):
            html = (_DIR / "login.html").read_text(encoding="utf-8").replace("{{BAD}}", "block" if "bad" in query else "none")
            return await _send(send, 200, html.encode(), "text/html; charset=utf-8")
        return await _send(send, 401, b'{"error":"not signed in"}', "application/json")

    if sub in ("/", ""):
        html = (_DIR / "index.html").read_text(encoding="utf-8").replace("{{VERSION}}", VERSION)
        return await _send(send, 200, html.encode(), "text/html; charset=utf-8")

    if sub == "/api/state":
        agent = await asyncio.to_thread(_agent_status)
        return await _send(send, 200, _json({"version": VERSION, "host": socket.gethostname(), "now": time.time(),
                                             "stats": store.stats(), "agent": agent, "log_file": str(LOG_FILE)}), "application/json")

    if sub == "/api/events":
        after = int(query.get("after", ["0"])[0] or 0)
        new = store.after(after, 400)
        cutoff = time.time() - 180
        recent = [e for e in store.after(max(0, after - 80), 200) if e["id"] <= after and e["kind"] == "tool" and e["ts"] > cutoff]
        return await _send(send, 200, _json({"events": new, "refreshed": recent, "last_id": store.seq}), "application/json")

    if sub.startswith("/api/image/"):
        try:
            hit = store.image(int(sub.rsplit("/", 1)[1]))
        except ValueError:
            hit = None
        if not hit:
            return await _send(send, 404, b"gone", "text/plain")
        data, ext = hit
        return await _send(send, 200, data, "image/jpeg" if ext in ("jpg", "jpeg") else f"image/{ext}")

    if sub == "/api/live":
        frame = await asyncio.to_thread(_live_frame)
        if frame is None:
            return await _send(send, 503, b'{"error":"desktop agent is not running"}', "application/json")
        return await _send(send, 200, frame, "image/jpeg")

    if sub == "/api/logs":
        n = max(20, min(int(query.get("n", ["200"])[0] or 200), 1000))
        return await _send(send, 200, _json({"server": _tail(LOG_FILE, n), "agent": _tail(AGENT_LOG, n)}), "application/json")

    return await _send(send, 404, b'{"error":"not found"}', "application/json")
