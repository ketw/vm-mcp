"""client.py — server-side bridge to the desktop agent.

  call(action, **params)   -> dict   (starts / restarts the agent when needed)
  to_screen / to_shot      coordinate scaling between the agent's screenshot space and real pixels
"""

from __future__ import annotations

import hashlib
import json
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Optional

from .. import VERSION
from ..config import AGENT_LOG, AGENT_PORT, API_KEY, CURSOR_PX, INSTALL_DIR, SCREENSHOT_MAX_WIDTH
from ..logs import log
from ..winutil import current_session_id, get_interactive_session_id, launch_in_session

_BASE = f"http://127.0.0.1:{AGENT_PORT}"
_launch_lock = threading.Lock()
# Never route localhost traffic through an HTTP(S)_PROXY from the environment.
_opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
_last_failure: Optional[tuple[float, str]] = None       # (when, message) — cooldown for a broken agent
_COOLDOWN = 20.0


class ComputerError(RuntimeError):
    pass


def token() -> str:
    return hashlib.sha256(f"vmmcp-agent:{API_KEY}".encode()).hexdigest()[:32]


def _post(endpoint: str, payload: dict, timeout: float, auth: bool = True) -> dict:
    req = urllib.request.Request(f"{_BASE}/{endpoint}", data=json.dumps(payload).encode(), method="POST",
                                 headers={"Content-Type": "application/json", **({"X-Agent-Token": token()} if auth else {})})
    try:
        with _opener.open(req, timeout=timeout) as r:
            return json.loads(r.read())
    except urllib.error.HTTPError as exc:
        try:
            return json.loads(exc.read())
        except Exception:
            return {"ok": False, "error": f"HTTP {exc.code}"}


def _ping() -> Optional[dict]:
    try:
        r = _post("ping", {}, 1.5, auth=False)
        return r if r.get("alive") else None
    except Exception:
        return None


def _spawn(session: int) -> dict:
    exe = Path(sys.executable)
    exe = exe.with_name("pythonw.exe") if exe.with_name("pythonw.exe").exists() else exe   # no console window
    args = f"-m vmmcp.computer.agent --port {AGENT_PORT} --token {token()} --cursor-px {CURSOR_PX}"
    if current_session_id() == session:                    # server already lives on the user's desktop
        subprocess.Popen([str(exe)] + args.split(), cwd=str(INSTALL_DIR), creationflags=0x08000008)  # NO_WINDOW|DETACHED
        return {"started": True}
    return launch_in_session(str(exe), args, session_id=session, cwd=str(INSTALL_DIR))


def _agent_log_tail(n: int = 12) -> str:
    try:
        lines = AGENT_LOG.read_text(encoding="utf-8", errors="replace").strip().splitlines()
        return "\n".join(lines[-n:])
    except OSError:
        return "(no agent log found)"


def ensure_agent() -> dict:
    """Make sure a current-version agent is running on the active console session.

    Must stay FAST: it runs on every computer-use call. Worst case is ~8 s on a cold start, and a broken agent
    is not retried for 20 s (the error is replayed) so a bad install can never pile up long waits.
    """
    global _last_failure
    with _launch_lock:
        session = get_interactive_session_id()
        if session == 0xFFFFFFFF:
            raise ComputerError("Nobody is logged in on the VM console, so there is no desktop to control.")
        p = _ping()
        if p and p.get("version") == VERSION and p.get("session") == session:
            return p
        if _last_failure and time.monotonic() - _last_failure[0] < _COOLDOWN and not p:
            raise ComputerError(_last_failure[1] + "  (retrying automatically in a few seconds)")
        if p:                                              # stale: old version or wrong session
            log.info("restarting desktop agent (have v%s/session %s, want v%s/session %s)",
                     p.get("version"), p.get("session"), VERSION, session)
            stop_agent()
            time.sleep(0.8)
        r = _spawn(session)
        if not r.get("started"):
            msg = f"Could not start the desktop agent: {r.get('error')}"
            _last_failure = (time.monotonic(), msg)
            raise ComputerError(msg)
        for _ in range(32):                                # 8 s
            time.sleep(0.25)
            p = _ping()
            if p:
                _last_failure = None
                return p
        msg = ("Desktop agent was launched but never answered within 8 s. Agent log:\n" + _agent_log_tail() +
               f"\n(Full log: {AGENT_LOG}. Common cause: Pillow not installed for {sys.executable}.)")
        _last_failure = (time.monotonic(), msg)
        raise ComputerError(msg)


def stop_agent() -> bool:
    """Best-effort shutdown of the desktop agent (it restarts on the next computer-use call)."""
    try:
        return bool(_post("shutdown", {}, 2).get("ok"))
    except Exception:
        return False


def call(action: str, timeout: float = 60.0, **params) -> dict:
    ensure_agent()
    try:
        r = _post(action, params, timeout)
    except Exception as exc:
        raise ComputerError(f"Desktop agent call '{action}' failed: {exc}")
    if not r.get("ok"):
        raise ComputerError(r.get("error", "unknown agent error"))
    return r


# ── Coordinate scaling ───────────────────────────────────────────────────────
# Agents always speak "screenshot pixels". scale = real screen px per screenshot px
# (1.0 unless the screen is wider than SCREENSHOT_MAX_WIDTH).
_scale = 1.0
_screen: Optional[tuple[int, int]] = None


def update_screen(width: int, height: int, out_width: Optional[int] = None) -> float:
    global _scale, _screen
    _screen = (width, height)
    _scale = width / min(out_width or SCREENSHOT_MAX_WIDTH, width)
    return _scale


def refresh_screen() -> tuple[int, int]:
    """Start the agent if needed and learn the real screen size (re-derives scale if it changed)."""
    w, h = ensure_agent()["screen"]
    if _screen != (w, h):
        update_screen(w, h)
    return w, h


def scale() -> float:
    refresh_screen()
    return _scale


def to_screen(v: float) -> int:
    """agent (screenshot) px -> real px"""
    return int(round(v * scale()))


def to_shot(v: float) -> int:
    """real px -> agent (screenshot) px"""
    return int(round(v / scale()))
