"""tools/clipboard.py — Clipboard."""

from __future__ import annotations

from ..mcp_instance import mcp
from ..winutil import run_ps


@mcp.tool()
def clipboard_get() -> dict:
    """Return current clipboard text."""
    r = run_ps("Get-Clipboard")
    return {"text": r["stdout"], "exit_code": r["exit_code"]}


@mcp.tool()
def clipboard_set(text: str) -> dict:
    """Write text to the VM clipboard."""
    r = run_ps(f"Set-Clipboard -Value '{text.replace(chr(39), chr(39)*2)}'")
    return {"success": r["exit_code"] == 0, "stderr": r["stderr"]}
