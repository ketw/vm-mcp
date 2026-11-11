"""tools/system.py — System info, event log, scheduled tasks, env vars, drivers, toast."""

from __future__ import annotations

import httpx
import json
import platform
import socket
import sys
import time
from datetime import datetime
from typing import Optional
from .. import VERSION
from ..config import PORT
from ..mcp_instance import mcp
from ..winutil import run_ps


@mcp.tool()
def vm_info() -> dict:
    """
    Comprehensive VM system information.

    Returns: {os, cpu, ram, disk, uptime, hostname, public_ip, python}
    """
    hostname = socket.gethostname()
    public_ip = ""
    try:
        public_ip = httpx.get("https://api.ipify.org", timeout=5).text.strip()
    except Exception:
        pass

    # CPU / RAM / Disk via psutil if available
    try:
        import psutil
        cpu = {
            "count_physical": psutil.cpu_count(logical=False),
            "count_logical":  psutil.cpu_count(logical=True),
            "percent":        psutil.cpu_percent(interval=0.5),
            "freq_mhz":       round(psutil.cpu_freq().current, 0) if psutil.cpu_freq() else None,
        }
        vm  = psutil.virtual_memory()
        ram = {"total_gb": round(vm.total/1e9,2), "used_gb": round(vm.used/1e9,2), "percent": vm.percent}
        disk_c = psutil.disk_usage("C:\\")
        disk = {"total_gb": round(disk_c.total/1e9,1), "used_gb": round(disk_c.used/1e9,1), "free_gb": round(disk_c.free/1e9,1), "percent": disk_c.percent}
        boot_ts = psutil.boot_time()
        uptime_h = round((time.time() - boot_ts) / 3600, 1)
    except ImportError:
        r_cpu  = run_ps("(Get-CimInstance Win32_Processor | Measure-Object -Property NumberOfCores -Sum).Sum")
        r_ram  = run_ps("(Get-CimInstance Win32_ComputerSystem).TotalPhysicalMemory")
        r_disk = run_ps("Get-PSDrive C | Select-Object @{N='U';E={[math]::Round($_.Used/1GB,1)}},@{N='F';E={[math]::Round($_.Free/1GB,1)}} | ConvertTo-Json -Compress")
        r_up   = run_ps("(Get-Date) - (gcim Win32_OperatingSystem).LastBootUpTime | Select-Object -ExpandProperty TotalHours")
        cpu    = {"count_physical": r_cpu["stdout"].strip()}
        try:
            ram_b = int(r_ram["stdout"].strip())
            ram   = {"total_gb": round(ram_b/1e9, 2)}
        except Exception: ram = {}
        try: disk = json.loads(r_disk["stdout"])
        except Exception: disk = {}
        try: uptime_h = round(float(r_up["stdout"].strip()), 1)
        except Exception: uptime_h = None

    return {
        "hostname":   hostname,
        "os":         f"Windows {platform.release()} build {platform.version()}",
        "arch":       platform.machine(),
        "cpu":        cpu,
        "ram":        ram,
        "disk_c":     disk,
        "uptime_hours": uptime_h,
        "public_ip":  public_ip,
        "python":     sys.version,
        "mcp_version": VERSION,
        "mcp_port":   PORT,
        "timestamp":  datetime.now().isoformat(),
    }


@mcp.tool()
def event_log_read(
    log_name: str = "System",
    source: str = "",
    level: str = "",
    max_entries: int = 50,
    hours_back: int = 24,
) -> dict:
    """
    Read Windows Event Log entries.

    Args:
        log_name: Log name — System (default), Application, Security.
        source: Optional source/provider filter (e.g. NemoKmd).
        level: Optional level filter — Error, Warning, Information.
        max_entries: Maximum entries to return (default 50).
        hours_back: Look back this many hours (default 24).

    Returns: {events, count, log_name}
    """
    when = f"(Get-Date).AddHours(-{hours_back})"
    cmd = (
        f"$since = (Get-Date).AddHours(-{hours_back}); "
        f"$filter = @{{LogName='{log_name}'; StartTime=$since}}; "
    )
    if level:
        level_map = {"Error": 2, "Warning": 3, "Information": 4}
        lvl = level_map.get(level, 4)
        cmd += f"$filter['Level'] = {lvl}; "
    cmd += (
        f"Get-WinEvent -FilterHashtable $filter -MaxEvents {max_entries * 3} -ErrorAction SilentlyContinue "
    )
    if source: cmd += f"| Where-Object {{ $_.ProviderName -like '*{source}*' }} "
    cmd += (
        f"| Select-Object -First {max_entries} "
        "| Select-Object TimeCreated,LevelDisplayName,ProviderName,Id,"
        "@{N='Message';E={$_.Message -replace '`r`n',' ' -replace '`n',' '}} "
        "| ConvertTo-Json -Compress"
    )
    r = run_ps(cmd, timeout=30)
    try:
        events = json.loads(r["stdout"])
        if isinstance(events, dict): events = [events]
        return {"events": events, "count": len(events), "log_name": log_name}
    except Exception:
        return {"raw": r["stdout"][:2000], "error": r["stderr"]}


@mcp.tool()
def scheduled_task_list(filter_name: str = "", task_path: str = "\\") -> dict:
    """
    List scheduled tasks.

    Args:
        filter_name: Optional name substring filter.
        task_path: Task folder path (default \\).

    Returns: {tasks, count}
    """
    cmd = f"Get-ScheduledTask -TaskPath '{task_path}*'"
    if filter_name: cmd += f" | Where-Object {{ $_.TaskName -like '*{filter_name}*' }}"
    cmd += " | Select-Object TaskName,TaskPath,State | ConvertTo-Json -Compress"
    r = run_ps(cmd, timeout=20)
    try:
        tasks = json.loads(r["stdout"])
        if isinstance(tasks, dict): tasks = [tasks]
        return {"tasks": tasks, "count": len(tasks)}
    except Exception:
        return {"raw": r["stdout"], "error": r["stderr"]}


@mcp.tool()
def scheduled_task_run(name: str) -> dict:
    """Immediately run a scheduled task by name."""
    r = run_ps(f"Start-ScheduledTask -TaskName '{name}' -ErrorAction Stop")
    return {"started": r["exit_code"] == 0, "name": name, "stderr": r["stderr"]}


@mcp.tool()
def env_get(name: str, scope: str = "Process") -> dict:
    """
    Read an environment variable.

    Args:
        name: Variable name.
        scope: Process (default), User, or Machine.

    Returns: {name, value, scope}
    """
    r = run_ps(f"[System.Environment]::GetEnvironmentVariable('{name}', '{scope}')")
    return {"name": name, "value": r["stdout"].strip() or None, "scope": scope}


@mcp.tool()
def env_set(name: str, value: Optional[str], scope: str = "Machine") -> dict:
    """
    Set or delete a system environment variable.

    Args:
        name: Variable name.
        value: New value. Pass null/None to delete the variable.
        scope: Machine (default), User, or Process.

    Returns: {success, name, value, scope}
    """
    if value is None:
        r = run_ps(f"[System.Environment]::SetEnvironmentVariable('{name}', $null, '{scope}')")
    else:
        r = run_ps(f"[System.Environment]::SetEnvironmentVariable('{name}', '{value.replace(chr(39), chr(39)*2)}', '{scope}')")
    return {"success": r["exit_code"] == 0, "name": name, "value": value, "scope": scope, "stderr": r["stderr"]}


@mcp.tool()
def installed_drivers(filter_name: str = "", state_filter: str = "") -> dict:
    """
    List installed kernel-mode drivers with their state and start type.

    Args:
        filter_name: Optional name substring filter (e.g. 'Nemo', 'vmware').
        state_filter: Optional state filter — Running or Stopped.

    Returns: {drivers, count}
    """
    ps = (
        "Get-WmiObject Win32_SystemDriver | "
        "Select-Object Name,DisplayName,State,StartMode,PathName | "
        "Sort-Object Name"
    )
    if filter_name:
        ps += f" | Where-Object {{ $_.Name -like '*{filter_name}*' -or $_.DisplayName -like '*{filter_name}*' }}"
    if state_filter:
        ps += f" | Where-Object {{ $_.State -eq '{state_filter}' }}"
    ps += " | ConvertTo-Json -Compress"
    r = run_ps(ps, timeout=30)
    try:
        drivers = json.loads(r["stdout"])
        if isinstance(drivers, dict): drivers = [drivers]
        return {"drivers": drivers, "count": len(drivers)}
    except Exception:
        return {"raw": r["stdout"][:3000], "error": r["stderr"]}

@mcp.tool()
def toast_notify(title: str, message: str, duration: int = 5) -> dict:
    """
    Show a Windows toast notification on the VM desktop.

    Args:
        title: Notification title.
        message: Notification body text.
        duration: Display duration hint in seconds (Windows may ignore this).

    Returns: {shown}
    """
    ps = f"""
Add-Type -AssemblyName System.Windows.Forms
$notify = New-Object System.Windows.Forms.NotifyIcon
$notify.Icon = [System.Drawing.SystemIcons]::Information
$notify.Visible = $true
$notify.ShowBalloonTip({duration * 1000}, '{title.replace("'","''")}', '{message.replace("'","''")}', [System.Windows.Forms.ToolTipIcon]::Info)
Start-Sleep -Milliseconds 500
$notify.Dispose()
"""
    r = run_ps(ps, timeout=max(duration + 3, 10))
    return {"shown": r["exit_code"] == 0, "stderr": r["stderr"]}
