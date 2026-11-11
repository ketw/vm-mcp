"""tools/power.py — Power: reboot / shutdown."""

from __future__ import annotations

from ..mcp_instance import mcp
from ..winutil import run_ps


@mcp.tool()
def reboot(delay_seconds: int = 10, message: str = "VM MCP reboot") -> dict:
    """Schedule a VM reboot."""
    delay_seconds = max(0, min(delay_seconds, 3600))
    r = run_ps(f'shutdown.exe /r /t {delay_seconds} /c "{message}"')
    return {"scheduled": r["exit_code"] == 0, "delay_seconds": delay_seconds, "stderr": r["stderr"].strip()}


@mcp.tool()
def reboot_cancel() -> dict:
    """Cancel a pending shutdown or reboot."""
    r = run_ps("shutdown.exe /a")
    return {"cancelled": r["exit_code"] == 0, "stderr": r["stderr"].strip()}


@mcp.tool()
def shutdown(delay_seconds: int = 10, message: str = "VM MCP shutdown") -> dict:
    """Schedule a VM shutdown (power off)."""
    delay_seconds = max(0, min(delay_seconds, 3600))
    r = run_ps(f'shutdown.exe /s /t {delay_seconds} /c "{message}"')
    return {"scheduled": r["exit_code"] == 0, "delay_seconds": delay_seconds, "stderr": r["stderr"].strip()}
