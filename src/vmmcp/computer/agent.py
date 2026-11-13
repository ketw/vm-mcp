"""agent.py — the desktop agent. Runs on the user's interactive desktop, NOT in the service session.

    python -m vmmcp.computer.agent --port 8788 --token <secret>

It owns the AI cursor overlay, real input injection and screen capture, and exposes them
over a tiny localhost-only JSON/HTTP API that client.py (inside the MCP server) calls.
Stdlib + Pillow + ctypes only.  Log: %PROGRAMDATA%\\vm-mcp\\agent.log
"""

from __future__ import annotations

import argparse
import base64
import ctypes
import json
import logging
import os
import sys
import tempfile
import threading
import traceback
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from .. import VERSION

log = logging.getLogger("vm-mcp-agent")


def _set_dpi_awareness() -> float:
    """Physical-pixel coordinates everywhere. Returns UI scale (1.0 = 96 dpi)."""
    user32 = ctypes.windll.user32
    try:
        user32.SetProcessDpiAwarenessContext(ctypes.c_void_p(-4))        # per-monitor v2
    except Exception:
        try:
            ctypes.windll.shcore.SetProcessDpiAwareness(2)
        except Exception:
            user32.SetProcessDPIAware()
    scale = 1.0
    try:
        scale = max(1.0, user32.GetDpiForSystem() / 96.0)          # display scaling (125% -> 1.25 ...)
    except Exception:
        pass
    try:                                                          # Settings > Accessibility > Mouse pointer size
        import winreg
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, r"Control Panel\Cursors") as k:
            base = int(winreg.QueryValueEx(k, "CursorBaseSize")[0])
        scale *= max(0.5, base / 32.0)
    except Exception:
        pass
    return scale


class AgentState:
    def __init__(self, token: str, port: int, cursor_px: float = 19.0):
        from . import inputs
        from .actor import Actor
        from .overlay import Overlay
        self.token, self.port = token, port
        self.ui_scale = _set_dpi_awareness_cached()
        self.overlay = Overlay(self.ui_scale, on_move=inputs.move_abs, cursor_px=cursor_px)
        self.overlay.start()
        self.overlay.call(lambda e: (e.place(*inputs.cursor_pos()), e.fade_to(1.0)))
        self.actor = Actor(self.overlay)
        self.session = _session_id()
        self.show_cursor = True


_dpi_cache: list[float] = []


def _set_dpi_awareness_cached() -> float:
    if not _dpi_cache:
        _dpi_cache.append(_set_dpi_awareness())
    return _dpi_cache[0]


def _session_id() -> int:
    sid = ctypes.c_ulong(0)
    ctypes.windll.kernel32.ProcessIdToSessionId(os.getpid(), ctypes.byref(sid))
    return sid.value


# ── Endpoint handlers: each takes a dict, returns a dict ─────────────────────
def build_handlers(st: AgentState, shutdown_cb):
    a = st.actor

    def ping(_):
        from . import inputs
        _, _, sw, sh = inputs.virtual_rect()
        return {"alive": True, "version": VERSION, "session": st.session, "pid": os.getpid(),
                "ui_scale": st.ui_scale, "screen": [sw, sh]}

    def screenshot(p):
        data, meta = a.screenshot(p.get("region"), p.get("out_width"), p.get("label_scale", 1.0), p.get("grid", False),
                                  p.get("hide_cursor", False), p.get("format", "auto"), p.get("quality", 78),
                                  p.get("settle", False))
        return {**meta, "image_b64": base64.b64encode(data).decode("ascii")}

    def cursor(p):
        action = p.get("action", "status")
        if action == "show":
            st.show_cursor = True
            st.overlay.call(lambda e: e.fade_to(1.0))
        elif action == "hide":
            st.show_cursor = False
            st.overlay.call(lambda e: e.fade_to(0.0))
        elif action == "test":
            a.fail()
        return {"visible": st.show_cursor, **a.pos(), "ui_scale": st.ui_scale}

    return {
        "ping": ping,
        "info": lambda p: a.info(),
        "screenshot": screenshot,
        "move": lambda p: a.move(p["x"], p["y"], p.get("duration")),
        "click": lambda p: a.click(p["x"], p["y"], p.get("button", "left"), p.get("count", 1), p.get("modifiers")),
        "drag": lambda p: a.drag(p["x1"], p["y1"], p["x2"], p["y2"], p.get("button", "left"), p.get("path"), p.get("duration")),
        "hold": lambda p: a.hold(p["x"], p["y"], p.get("seconds", 1.0), p.get("button", "left")),
        "button": lambda p: a.button(p["state"], p.get("x"), p.get("y"), p.get("button", "left")),
        "scroll": lambda p: a.scroll(p["x"], p["y"], p.get("direction", "down"), p.get("amount", 3)),
        "type": lambda p: a.type_text(p["text"], p.get("delay_ms", 8), p.get("submit", False)),
        "key": lambda p: a.key(p["keys"], p.get("repeat", 1)),
        "wait": lambda p: a.wait(p.get("seconds", 1.0)),
        "focus": lambda p: a.focus(p.get("title", "")),
        "ui_tree": lambda p: a.ui_tree(p.get("max_items", 250)),
        "cursor": cursor,
        "shutdown": lambda p: (shutdown_cb(), {"stopping": True})[1],
    }


def make_server(st: AgentState) -> ThreadingHTTPServer:
    holder: dict = {}
    handlers = build_handlers(st, lambda: threading.Thread(target=lambda: (holder["srv"].shutdown(), os._exit(0)), daemon=True).start())

    class H(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def _send(self, obj: dict, code: int = 200):
            body = json.dumps(obj).encode()
            self.send_response(code)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def do_POST(self):
            name = self.path.strip("/")
            if name != "ping" and self.headers.get("X-Agent-Token") != st.token:
                return self._send({"ok": False, "error": "bad token"}, 401)
            fn = handlers.get(name)
            if not fn:
                return self._send({"ok": False, "error": f"unknown endpoint {name}"}, 404)
            try:
                n = int(self.headers.get("Content-Length", 0))
                params = json.loads(self.rfile.read(n) or b"{}")
                result = fn(params)
                self._send({"ok": True, **result})
            except Exception as exc:
                log.error("%s failed: %s\n%s", name, exc, traceback.format_exc())
                if name not in ("ping", "info", "screenshot", "ui_tree", "cursor"):
                    st.actor.fail()
                self._send({"ok": False, "error": f"{type(exc).__name__}: {exc}"})

    srv = ThreadingHTTPServer(("127.0.0.1", st.port), H)
    srv.daemon_threads = True
    holder["srv"] = srv
    return srv


def _setup_log() -> None:
    """Log to %PROGRAMDATA%\\vm-mcp\\agent.log (the server reads it to explain failures); fall back to TEMP."""
    fmt = "%(asctime)s %(levelname)s %(message)s"
    for d in (os.path.join(os.environ.get("PROGRAMDATA", r"C:\\ProgramData"), "vm-mcp"), tempfile.gettempdir()):
        try:
            os.makedirs(d, exist_ok=True)
            logging.basicConfig(filename=os.path.join(d, "agent.log"), level=logging.INFO, format=fmt)
            return
        except OSError:
            continue


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", type=int, default=8788)
    ap.add_argument("--token", required=True)
    ap.add_argument("--cursor-px", type=float, default=19.0)
    args = ap.parse_args()
    _setup_log()
    try:
        st = AgentState(args.token, args.port, args.cursor_px)
        srv = make_server(st)
    except BaseException as exc:                # port busy, Pillow missing, window creation failed ...
        log.error("cannot start: %s\n%s", exc, traceback.format_exc())
        sys.exit(2)
    log.info("agent %s up on 127.0.0.1:%d (session %d, ui_scale %.2f)", VERSION, args.port, st.session, st.ui_scale)
    srv.serve_forever()


if __name__ == "__main__":
    main()
