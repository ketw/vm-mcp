"""tools/services.py — Windows services."""

from __future__ import annotations

import json
from ..mcp_instance import mcp
from ..winutil import run_ps


@mcp.tool()
def service_status(name: str) -> dict:
    """Get the status of a Windows service."""
    r = run_ps(
        f"Get-Service -Name '{name}' -ErrorAction SilentlyContinue | "
        f"Select-Object Name,DisplayName,Status,StartType | ConvertTo-Json -Compress"
    )
    try:
        return json.loads(r["stdout"]) or {"error": f"Service not found: {name}"}
    except Exception:
        return {"raw": r["stdout"], "error": r["stderr"]}


@mcp.tool()
def service_control(name: str, action: str) -> dict:
    """
    Start, stop, restart, or pause a Windows service.

    Args:
        name: Service name.
        action: start | stop | restart | pause | resume.

    Returns: {success, status, stderr}
    """
    cmds = {
        "start":   f"Start-Service   -Name '{name}' -ErrorAction Stop",
        "stop":    f"Stop-Service    -Name '{name}' -Force -ErrorAction Stop",
        "restart": f"Restart-Service -Name '{name}' -Force -ErrorAction Stop",
        "pause":   f"Suspend-Service -Name '{name}' -ErrorAction Stop",
        "resume":  f"Resume-Service  -Name '{name}' -ErrorAction Stop",
    }
    cmd = cmds.get(action.lower())
    if not cmd:
        return {"error": f"Unknown action: {action!r}. Use: {list(cmds)}"}
    r = run_ps(cmd)
    s = run_ps(f"(Get-Service -Name '{name}' -ErrorAction SilentlyContinue).Status")
    return {"success": r["exit_code"] == 0, "status": s["stdout"].strip(), "stderr": r["stderr"].strip()}


@mcp.tool()
def service_list(filter_name: str = "", status_filter: str = "") -> dict:
    """
    List all Windows services.

    Args:
        filter_name: Optional name substring filter.
        status_filter: Optional status filter — Running, Stopped, Paused.

    Returns: {services, count}
    """
    cmd = "Get-Service"
    if filter_name: cmd += f" -Name '*{filter_name}*'"
    cmd += " | Select-Object Name,DisplayName,Status,StartType | ConvertTo-Json -Compress"
    r = run_ps(cmd, timeout=30)
    try:
        svcs = json.loads(r["stdout"])
        if isinstance(svcs, dict): svcs = [svcs]
        if status_filter:
            svcs = [s for s in svcs if (s.get("Status","") or "").lower() == status_filter.lower()]
        return {"services": svcs, "count": len(svcs)}
    except Exception:
        return {"raw": r["stdout"], "error": r["stderr"]}
