"""tools/server_admin.py — version, self-update (whole src/ tree), restart, log tail."""

from __future__ import annotations

import io
import os
import sys
import threading
import time
import zipfile
from pathlib import Path

import httpx

from .. import VERSION
from ..computer import client
from ..config import INSTALL_DIR, LOG_FILE, UPDATE_URL
from ..logs import log
from ..mcp_instance import mcp


def _reexec_later(delay: float) -> None:
    def go():
        time.sleep(delay)
        log.info("re-exec (pid %d)", os.getpid())
        os.execv(sys.executable, [sys.executable] + sys.argv)
    threading.Thread(target=go, daemon=True).start()


@mcp.tool()
def server_version() -> dict:
    """Server version, install dir, pid, Python version, update URL and log path."""
    return {"version": VERSION, "install_dir": str(INSTALL_DIR), "pid": os.getpid(),
            "python": sys.version, "update_url": UPDATE_URL, "log_file": str(LOG_FILE)}


@mcp.tool()
def server_update(restart: bool = False, url: str = "") -> dict:
    """
    Download the repo zip from GitHub and replace everything under the install dir with its `src/` folder
    (vm-mcp.log and other untracked files are kept). Stops the desktop agent so it reloads with the new code.

    Args:
        restart: Restart the server afterwards (recommended).
        url: Override the zip URL (default VM_MCP_UPDATE_URL, the GitHub master archive).
    """
    target = url or UPDATE_URL
    log.info("server_update: fetching %s", target)
    try:
        resp = httpx.get(target, timeout=60, follow_redirects=True)
        resp.raise_for_status()
        zf = zipfile.ZipFile(io.BytesIO(resp.content))
    except Exception as exc:
        return {"updated": False, "error": f"download failed: {exc}"}

    root = INSTALL_DIR.resolve()
    written = 0
    for info in zf.infolist():
        parts = Path(info.filename).parts
        if info.is_dir() or "src" not in parts[:2]:
            continue
        rel = Path(*parts[parts.index("src") + 1:])
        dest = (root / rel).resolve()
        if root not in dest.parents:                       # zip-slip guard
            continue
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_bytes(zf.read(info))
        written += 1
    if written == 0:
        return {"updated": False, "error": "zip contained no src/ files — wrong URL?"}

    client.stop_agent()
    if restart:
        _reexec_later(2.0)
    return {"updated": True, "files_written": written, "version_before": VERSION, "restarting": restart}


@mcp.tool()
def server_restart() -> dict:
    """Restart the server process (response is sent first). Use after server_update()."""
    _reexec_later(1.5)
    return {"restarting": True, "pid": os.getpid()}


@mcp.tool()
def server_log_tail(lines: int = 50) -> dict:
    """Last N lines (max 500) of the server log."""
    lines = min(lines, 500)
    if not LOG_FILE.exists():
        return {"log_file": str(LOG_FILE), "content": "", "lines_returned": 0}
    with open(LOG_FILE, encoding="utf-8", errors="replace") as f:
        all_lines = f.readlines()
    tail = all_lines[-lines:]
    return {"log_file": str(LOG_FILE), "content": "".join(tail), "lines_returned": len(tail),
            "total_lines": len(all_lines)}
