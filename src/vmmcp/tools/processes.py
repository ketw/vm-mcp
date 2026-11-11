"""tools/processes.py — Processes."""

from __future__ import annotations

import json
import subprocess
from datetime import datetime
from typing import Optional
from ..config import CMD
from ..mcp_instance import mcp
from ..winutil import launch_in_session
from ..winutil import run_ps


@mcp.tool()
def process_list(filter_name: str = "", sort_by: str = "memory") -> dict:
    """
    List running processes.

    Args:
        filter_name: Case-insensitive name substring filter.
        sort_by: Sort key — memory (default), cpu, name, pid.

    Returns: {processes, count}
    """
    try:
        import psutil
        procs = []
        for p in psutil.process_iter(["pid", "name", "cpu_percent", "memory_info", "status", "create_time", "username"]):
            try:
                info = p.info
                name = info.get("name", "")
                if filter_name and filter_name.lower() not in name.lower():
                    continue
                mem = info.get("memory_info")
                ct  = info.get("create_time", 0)
                procs.append({
                    "pid":       info["pid"],
                    "name":      name,
                    "cpu_pct":   info.get("cpu_percent", 0),
                    "mem_mb":    round(mem.rss / 1_048_576, 1) if mem else 0,
                    "status":    info.get("status", ""),
                    "user":      info.get("username", ""),
                    "started":   datetime.fromtimestamp(ct).isoformat() if ct else "",
                })
            except Exception:
                pass
        sort_key = {"memory": "mem_mb", "cpu": "cpu_pct", "name": "name", "pid": "pid"}.get(sort_by, "mem_mb")
        procs.sort(key=lambda x: x.get(sort_key, 0) or 0, reverse=(sort_by in ("memory", "cpu")))
        return {"processes": procs, "count": len(procs)}
    except ImportError:
        r = run_ps(
            "Get-Process | Select-Object Id,ProcessName,CPU,"
            "@{N='MemMB';E={[math]::Round($_.WorkingSet64/1MB,1)}} | "
            "ConvertTo-Json -Compress"
        )
        try:
            raw = json.loads(r["stdout"])
            if isinstance(raw, dict): raw = [raw]
            filtered = [
                {"pid": p.get("Id"), "name": p.get("ProcessName"), "mem_mb": p.get("MemMB")}
                for p in raw
                if not filter_name or filter_name.lower() in (p.get("ProcessName","")).lower()
            ]
            return {"processes": filtered, "count": len(filtered)}
        except Exception:
            return {"raw": r["stdout"], "error": r["stderr"]}


@mcp.tool()
def process_start(
    executable: str,
    arguments: str = "",
    working_directory: str = "",
    hidden: bool = False,
    interactive: bool = False,
) -> dict:
    """
    Start a new process.

    Args:
        executable: Path to executable (e.g. C:\\Windows\\System32\\notepad.exe).
        arguments: Command-line arguments string.
        working_directory: Optional working directory.
        hidden: If True, start with hidden window style (headless/background).
        interactive: If True, launch in the interactive user desktop session so
                     the window is VISIBLE on the physical display.  Uses a
                     scheduled-task bridge to cross the Session-0 boundary.
                     Cannot be combined with hidden=True.

    Returns: {pid, started}
    """
    if interactive:
        # Launch directly into the interactive user session via CreateProcessAsUser
        result = launch_in_session(executable, arguments or "")
        return {
            "started": result["started"],
            "pid": result["pid"],
            "interactive": True,
            "error": result.get("error"),
            "note": "Launched in interactive user session — window will be visible on desktop" if result["started"] else "Launch failed",
        }

    # Headless / Session-0 launch (original behaviour)
    style = "-WindowStyle Hidden" if hidden else ""
    cmd = f'Start-Process -FilePath "{executable}"'
    if arguments:
        cmd += f' -ArgumentList "{arguments}"'
    if working_directory:
        cmd += f' -WorkingDirectory "{working_directory}"'
    if style:
        cmd += f" {style}"
    cmd += " -PassThru | Select-Object -ExpandProperty Id"
    r = run_ps(cmd)
    return {"started": r["exit_code"] == 0, "pid": r["stdout"].strip(), "stderr": r["stderr"]}


@mcp.tool()
def process_kill(pid: Optional[int] = None, name: str = "", force: bool = True) -> dict:
    """
    Kill a process by PID or name.

    Args:
        pid: Process ID (takes priority over name).
        name: Process name — kills ALL matching.
        force: Force-kill (default True).

    Returns: {killed, stderr}
    """
    if pid is not None:
        cmd = f"taskkill /PID {pid}{' /F' if force else ''}"
    elif name:
        # strip .exe suffix if provided, taskkill /IM needs the exe name
        exe = name if name.lower().endswith(".exe") else name + ".exe"
        cmd = f"taskkill /IM \"{exe}\"{' /F' if force else ''}"
    else:
        return {"error": "Provide pid or name"}
    result = subprocess.run(
        [CMD, "/c", cmd],
        capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=15,
    )
    return {"killed": result.returncode == 0, "stdout": result.stdout.strip(), "stderr": result.stderr.strip()}
