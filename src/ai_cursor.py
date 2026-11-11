"""
ai_cursor.py — AI Cursor Overlay for vm-mcp
============================================

A transparent always-on-top tkinter window that draws a purple AI cursor
arrow on the screen.  Controlled via a tiny HTTP server on localhost:8788.

API (all JSON POST):
  POST /move        {"x": int, "y": int}
  POST /show        {}
  POST /hide        {}
  POST /ping        {}  → {"alive": true}

The overlay detects the active console session at startup.  If the session
changes (user switches, locks, etc.) the MCP server will re-launch this
script into the new active session automatically.

Run as: python ai_cursor.py
"""

from __future__ import annotations

import json
import sys
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer

import tkinter as tk


# ── Config ────────────────────────────────────────────────────────────────────
PORT      = 8788
CURSOR_W  = 24   # canvas size
CURSOR_H  = 32

# Purple AI cursor colour
FILL  = "#9B30FF"
OUTL  = "#3D0070"

# ── Draw the cursor arrow on a canvas ─────────────────────────────────────────
# Classic arrow shape: 11 polygon points
ARROW = [
    0,  0,
    0,  20,
    5,  15,
    8,  22,
    11, 21,
    8,  14,
    14, 14,
]


def _make_cursor_window():
    """Create and return a transparent, always-on-top Tk window with the cursor."""
    root = tk.Tk()
    root.overrideredirect(True)             # no title bar / borders
    root.wm_attributes("-topmost", True)    # always on top
    root.wm_attributes("-transparentcolor", "black")  # black = transparent
    root.configure(bg="black")
    root.geometry(f"{CURSOR_W}x{CURSOR_H}+0+0")

    canvas = tk.Canvas(root, width=CURSOR_W, height=CURSOR_H,
                       bg="black", highlightthickness=0)
    canvas.pack()

    # Draw purple arrow
    canvas.create_polygon(
        ARROW,
        fill=FILL, outline=OUTL, width=1.5,
        smooth=False,
    )
    # Small purple dot at tip
    canvas.create_oval(0, 0, 4, 4, fill=FILL, outline=OUTL)

    root.lift()
    return root


class _State:
    x: int = 100
    y: int = 100
    visible: bool = True
    root: tk.Tk | None = None


state = _State()


def _move_to(x: int, y: int):
    state.x = x
    state.y = y
    if state.root:
        state.root.after(0, lambda: state.root.geometry(f"{CURSOR_W}x{CURSOR_H}+{x}+{y}"))


def _set_visible(v: bool):
    state.visible = v
    if state.root:
        def _do():
            if v:
                state.root.deiconify()
                state.root.lift()
            else:
                state.root.withdraw()
        state.root.after(0, _do)


# ── HTTP control server ────────────────────────────────────────────────────────
class _Handler(BaseHTTPRequestHandler):
    def log_message(self, *args):
        pass   # silence request logs

    def _respond(self, data: dict, code: int = 200):
        body = json.dumps(data).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _read_json(self):
        length = int(self.headers.get("Content-Length", 0))
        return json.loads(self.rfile.read(length)) if length else {}

    def do_POST(self):
        path = self.path.rstrip("/")
        body = self._read_json()
        if path == "/move":
            x, y = int(body.get("x", state.x)), int(body.get("y", state.y))
            _move_to(x, y)
            self._respond({"moved": True, "x": x, "y": y})
        elif path == "/show":
            _set_visible(True)
            self._respond({"visible": True})
        elif path == "/hide":
            _set_visible(False)
            self._respond({"visible": False})
        elif path == "/ping":
            self._respond({"alive": True, "x": state.x, "y": state.y, "visible": state.visible})
        else:
            self._respond({"error": "unknown endpoint"}, 404)


def _run_http():
    server = HTTPServer(("127.0.0.1", PORT), _Handler)
    server.serve_forever()


# ── Main ──────────────────────────────────────────────────────────────────────
if __name__ == "__main__":
    # Start HTTP server in background thread
    t = threading.Thread(target=_run_http, daemon=True)
    t.start()

    # Build tkinter window on main thread (required on Windows)
    root = _make_cursor_window()
    state.root = root

    # Keep window on top via periodic lift
    def _keep_on_top():
        if state.visible and state.root:
            try:
                state.root.lift()
                state.root.wm_attributes("-topmost", True)
            except Exception:
                pass
        root.after(500, _keep_on_top)

    root.after(500, _keep_on_top)
    root.mainloop()
