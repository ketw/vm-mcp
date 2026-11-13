"""config.py — every environment variable, path and constant in one place.

Nothing here has side effects except reading os.environ, so it is safe to
import from tests.  The API-key check lives in app.main(), not here.
"""

from __future__ import annotations

import ipaddress
import os
from pathlib import Path

from . import VERSION  # noqa: F401  (re-exported for convenience)

# ── Paths ────────────────────────────────────────────────────────────────────
PACKAGE_DIR: Path = Path(__file__).resolve().parent      # .../src/vmmcp
INSTALL_DIR: Path = PACKAGE_DIR.parent                   # .../src  (== C:\VmMcp when installed)
LOG_FILE: Path = Path(os.environ.get("VM_MCP_LOG", str(INSTALL_DIR / "vm-mcp.log")))

# ── Server ───────────────────────────────────────────────────────────────────
API_KEY: str = os.environ.get("VM_MCP_API_KEY", "")
PORT: int = int(os.environ.get("VM_MCP_PORT", "8765"))
HOST: str = os.environ.get("VM_MCP_HOST", "0.0.0.0")
SHELL_TIMEOUT: int = int(os.environ.get("VM_MCP_SHELL_TIMEOUT", "120"))
RATE_LIMIT: int = int(os.environ.get("VM_MCP_RATE_LIMIT", "0"))  # requests/min per IP, 0 = off

# Self-update: a GitHub zip of the repo. The `src/` folder inside it replaces INSTALL_DIR.
UPDATE_URL: str = os.environ.get(
    "VM_MCP_UPDATE_URL",
    "https://github.com/ketw/vm-mcp/archive/refs/heads/master.zip",
)

# ── Desktop agent (the process that owns cursor + input + screen capture) ────
AGENT_PORT: int = int(os.environ.get("VM_MCP_AGENT_PORT", "8788"))
SCREENSHOT_MAX_WIDTH: int = int(os.environ.get("VM_MCP_SCREENSHOT_MAX_WIDTH", "1920"))
SCREENSHOT_JPEG_QUALITY: int = int(os.environ.get("VM_MCP_JPEG_QUALITY", "78"))
# 1 = every mouse/keyboard tool also returns a fresh screenshot (fewer round-trips for vision models)
AUTO_SCREENSHOT: bool = os.environ.get("VM_MCP_AUTO_SCREENSHOT", "0") == "1"

# Cursor size: visible arrow height in px at 100% scale. 19 = stock Windows 11 arrow. Auto-scaled by the
# user's display scaling and "Mouse pointer size" setting.
CURSOR_PX: float = float(os.environ.get("VM_MCP_CURSOR_PX", "19"))
# Hard ceiling for any single tool call (seconds). A stuck call returns an error instead of hanging the client.
TOOL_TIMEOUT: int = int(os.environ.get("VM_MCP_TOOL_TIMEOUT", "150"))
# Where the desktop agent writes its log (server reads it to explain start-up failures).
AGENT_LOG: Path = Path(os.environ.get("PROGRAMDATA", r"C:\ProgramData")) / "vm-mcp" / "agent.log"
ACTIVITY_DIR: Path = Path(os.environ.get("VM_MCP_ACTIVITY_DIR", str(INSTALL_DIR / "activity")))

# ── Full executable paths (SYSTEM has a minimal PATH) ────────────────────────
PWSH: str = r"C:\Windows\System32\WindowsPowerShell\v1.0\powershell.exe"
CMD: str = r"C:\Windows\System32\cmd.exe"
SCHTASKS: str = r"C:\Windows\System32\schtasks.exe"

# ── IP allowlist (VM_MCP_ALLOWED_IPS=1.2.3.4,10.0.0.0/8) ─────────────────────
ALLOWED_NETWORKS: list = []
for _entry in os.environ.get("VM_MCP_ALLOWED_IPS", "").split(","):
    _entry = _entry.strip()
    if _entry:
        try:
            ALLOWED_NETWORKS.append(ipaddress.ip_network(_entry, strict=False))
        except ValueError:
            print(f"[vm-mcp] WARN: bad IP/CIDR in allowlist: {_entry!r}")
