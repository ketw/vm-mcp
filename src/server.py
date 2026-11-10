"""
vm-mcp/src/server.py
====================
Remote VM control MCP server.

Runs on the target Windows VM and exposes the following MCP tools over HTTP/SSE
so Kiro CLI (or any MCP client) can connect from anywhere on the internet and
remotely control the machine.

Tools exposed
─────────────
  shell_run          Run a shell command (PowerShell), return stdout/stderr/exit
  shell_run_elevated Run a command elevated via a hidden scheduled-task trick
  file_read          Read a file (text or base64-encoded binary)
  file_write         Write / overwrite a file (text or binary)
  file_delete        Delete a file or directory
  file_list          List directory contents
  process_list       List running processes (name, pid, cpu%, mem)
  process_kill       Kill a process by pid or name
  screenshot         Capture the desktop as a base64-encoded PNG
  clipboard_get      Read clipboard text
  clipboard_set      Write text to clipboard
  registry_read      Read a registry value
  registry_write     Write a registry value
  service_status     Get status of a Windows service
  service_control    Start / stop / restart a Windows service
  install_msi        Download and install an MSI or EXE silently
  download_file      Download a URL to a local path on the VM
  vm_info            Return OS version, hostname, IP addresses, disk space, RAM
  reboot             Reboot the VM (optionally after a delay in seconds)

Security
────────
  Every request must carry the header:
    X-Api-Key: <key>
  The key is set via the VM_MCP_API_KEY environment variable (required).

  The server also supports IP allowlist via VM_MCP_ALLOWED_IPS (comma-separated
  CIDR or exact IPs). If unset, all IPs are allowed (key-only auth).

Transport
─────────
  HTTP/SSE on port VM_MCP_PORT (default 8765).
  The MCP client connects to:
    http://<vm-public-ip>:<port>/sse

Setup on VM
───────────
  1. pip install fastmcp httpx Pillow pywin32
  2. set VM_MCP_API_KEY=<a long random string>
  3. python server.py

  Or install as a Windows service — see install_service.ps1.
"""

import asyncio
import base64
import ctypes
import io
import ipaddress
import json
import os
import platform
import shutil
import socket
import subprocess
import sys
import tempfile
import winreg
from datetime import datetime
from pathlib import Path
from typing import Any

import httpx
from fastmcp import FastMCP
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import JSONResponse

# ─────────────────────────────────────────────────────────────────────────────
# Config from environment
# ─────────────────────────────────────────────────────────────────────────────
API_KEY: str = os.environ.get("VM_MCP_API_KEY", "")
if not API_KEY:
    sys.exit(
        "ERROR: VM_MCP_API_KEY environment variable is not set.\n"
        "Set it to a long random secret before starting the server.\n"
        "Example (PowerShell):\n"
        '  $env:VM_MCP_API_KEY = (New-Guid).ToString("N") + (New-Guid).ToString("N")\n'
        "  python server.py"
    )

PORT: int = int(os.environ.get("VM_MCP_PORT", "8765"))
HOST: str = os.environ.get("VM_MCP_HOST", "0.0.0.0")

ALLOWED_IPS_RAW: str = os.environ.get("VM_MCP_ALLOWED_IPS", "")
ALLOWED_NETWORKS: list[ipaddress.IPv4Network | ipaddress.IPv6Network] = []
if ALLOWED_IPS_RAW:
    for entry in ALLOWED_IPS_RAW.split(","):
        entry = entry.strip()
        if entry:
            try:
                ALLOWED_NETWORKS.append(ipaddress.ip_network(entry, strict=False))
            except ValueError:
                print(f"[vm-mcp] WARN: ignored invalid IP/CIDR in allowlist: {entry!r}")

# Default shell timeout in seconds
SHELL_TIMEOUT: int = int(os.environ.get("VM_MCP_SHELL_TIMEOUT", "120"))

# ─────────────────────────────────────────────────────────────────────────────
# MCP server instance
# ─────────────────────────────────────────────────────────────────────────────
mcp = FastMCP(
    name="vm-mcp",
    instructions=(
        "Remote Windows VM control server. "
        "Every tool call requires the API key to be supplied in X-Api-Key header. "
        "Use shell_run to execute PowerShell commands, file_* tools for file I/O, "
        "screenshot to see the desktop, service_control/install_msi to manage software."
    ),
)


# ─────────────────────────────────────────────────────────────────────────────
# Auth / IP-allowlist middleware (applied to the ASGI app FastMCP exposes)
# ─────────────────────────────────────────────────────────────────────────────
class AuthMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        # Health probe: no auth required
        if request.url.path in ("/health", "/ping"):
            from starlette.responses import PlainTextResponse
            return PlainTextResponse("ok")

        # IP allowlist check
        if ALLOWED_NETWORKS:
            client_ip_str = (request.client.host if request.client else "")
            try:
                client_ip = ipaddress.ip_address(client_ip_str)
                if not any(client_ip in net for net in ALLOWED_NETWORKS):
                    return JSONResponse(
                        {"error": "IP not in allowlist"},
                        status_code=403,
                    )
            except ValueError:
                return JSONResponse({"error": "Cannot parse client IP"}, status_code=403)

        # API key check
        key = request.headers.get("X-Api-Key", "")
        if key != API_KEY:
            return JSONResponse({"error": "Unauthorized"}, status_code=401)

        return await call_next(request)


# ─────────────────────────────────────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────────────────────────────────────
def _run_ps(command: str, timeout: int = SHELL_TIMEOUT, elevated: bool = False) -> dict:
    """Run a PowerShell command and return stdout/stderr/exit_code."""
    if elevated:
        # Run via schtasks trick so we get a SYSTEM-level elevated process
        task_name = f"VmMcpElevated_{datetime.now().strftime('%H%M%S%f')}"
        script_path = Path(tempfile.mktemp(suffix=".ps1"))
        out_path = Path(tempfile.mktemp(suffix=".txt"))
        script_path.write_text(
            f"{command}\n"
            f'$LASTEXITCODE | Out-File -FilePath "{out_path}" -Append\n',
            encoding="utf-8",
        )
        sched_cmd = (
            f'schtasks /Create /TN "{task_name}" /TR '
            f'"powershell.exe -ExecutionPolicy Bypass -NonInteractive -File \\"{script_path}\\"" '
            f"/SC ONCE /ST 00:00 /RU SYSTEM /F"
        )
        subprocess.run(sched_cmd, shell=True, capture_output=True)
        subprocess.run(f'schtasks /Run /TN "{task_name}"', shell=True, capture_output=True)
        import time
        time.sleep(min(timeout, 30))
        subprocess.run(f'schtasks /Delete /TN "{task_name}" /F', shell=True, capture_output=True)
        result_text = out_path.read_text(encoding="utf-8", errors="replace") if out_path.exists() else ""
        script_path.unlink(missing_ok=True)
        out_path.unlink(missing_ok=True)
        return {"stdout": result_text, "stderr": "", "exit_code": 0}

    result = subprocess.run(
        ["powershell.exe", "-ExecutionPolicy", "Bypass", "-NonInteractive", "-Command", command],
        capture_output=True,
        text=True,
        timeout=timeout,
        encoding="utf-8",
        errors="replace",
    )
    return {
        "stdout": result.stdout,
        "stderr": result.stderr,
        "exit_code": result.returncode,
    }


# ─────────────────────────────────────────────────────────────────────────────
# Tools — Shell
# ─────────────────────────────────────────────────────────────────────────────
@mcp.tool()
def shell_run(
    command: str,
    timeout_seconds: int = SHELL_TIMEOUT,
    working_directory: str = "",
) -> dict:
    """
    Run a PowerShell command on the VM and return the result.

    Args:
        command: PowerShell command or script block to execute.
        timeout_seconds: Maximum execution time (default 120 s). Max 600 s.
        working_directory: Optional directory to run the command from.

    Returns:
        {stdout, stderr, exit_code}
    """
    timeout_seconds = min(timeout_seconds, 600)
    if working_directory:
        command = f'Set-Location -Path "{working_directory}"; {command}'

    result = subprocess.run(
        ["powershell.exe", "-ExecutionPolicy", "Bypass", "-NonInteractive", "-Command", command],
        capture_output=True,
        text=True,
        timeout=timeout_seconds,
        encoding="utf-8",
        errors="replace",
    )
    return {
        "stdout": result.stdout.rstrip("\n"),
        "stderr": result.stderr.rstrip("\n"),
        "exit_code": result.returncode,
    }


@mcp.tool()
def shell_run_elevated(command: str, timeout_seconds: int = 60) -> dict:
    """
    Run a PowerShell command with SYSTEM-level elevation via scheduled task.
    Use this for operations that require admin/SYSTEM (driver install, bcdedit, etc.)

    Args:
        command: PowerShell command to run as SYSTEM.
        timeout_seconds: Wait time after scheduling (max 120 s).

    Returns:
        {stdout, stderr, exit_code}
    """
    return _run_ps(command, timeout=min(timeout_seconds, 120), elevated=True)


# ─────────────────────────────────────────────────────────────────────────────
# Tools — File I/O
# ─────────────────────────────────────────────────────────────────────────────
@mcp.tool()
def file_read(path: str, encoding: str = "utf-8", as_base64: bool = False) -> dict:
    """
    Read a file from the VM filesystem.

    Args:
        path: Absolute or relative path to the file.
        encoding: Text encoding (default utf-8). Ignored if as_base64=True.
        as_base64: If True, return raw bytes as base64 (for binary files).

    Returns:
        {content, size_bytes, path}
    """
    p = Path(path)
    if not p.exists():
        return {"error": f"File not found: {path}"}
    size = p.stat().st_size
    if as_base64:
        content = base64.b64encode(p.read_bytes()).decode("ascii")
    else:
        content = p.read_text(encoding=encoding, errors="replace")
    return {"content": content, "size_bytes": size, "path": str(p.resolve())}


@mcp.tool()
def file_write(
    path: str,
    content: str,
    encoding: str = "utf-8",
    from_base64: bool = False,
    create_dirs: bool = True,
) -> dict:
    """
    Write content to a file on the VM (creates or overwrites).

    Args:
        path: Destination path.
        content: Text content, or base64-encoded bytes if from_base64=True.
        encoding: Text encoding (default utf-8).
        from_base64: If True, decode content from base64 before writing.
        create_dirs: Create parent directories if they don't exist (default True).

    Returns:
        {path, size_bytes, created_dirs}
    """
    p = Path(path)
    created_dirs = False
    if create_dirs and not p.parent.exists():
        p.parent.mkdir(parents=True, exist_ok=True)
        created_dirs = True
    if from_base64:
        p.write_bytes(base64.b64decode(content))
    else:
        p.write_text(content, encoding=encoding)
    return {"path": str(p.resolve()), "size_bytes": p.stat().st_size, "created_dirs": created_dirs}


@mcp.tool()
def file_delete(path: str, recursive: bool = False) -> dict:
    """
    Delete a file or directory on the VM.

    Args:
        path: Path to delete.
        recursive: If True, delete directory and all contents. Default False.

    Returns:
        {deleted, path}
    """
    p = Path(path)
    if not p.exists():
        return {"error": f"Path not found: {path}"}
    if p.is_dir():
        if not recursive:
            return {"error": "Path is a directory; set recursive=True to delete it"}
        shutil.rmtree(p)
    else:
        p.unlink()
    return {"deleted": True, "path": str(p)}


@mcp.tool()
def file_list(
    path: str = ".",
    pattern: str = "*",
    recursive: bool = False,
    include_hidden: bool = False,
) -> dict:
    """
    List files and directories on the VM.

    Args:
        path: Directory to list (default current directory).
        pattern: Glob pattern (default *).
        recursive: If True, recurse into subdirectories.
        include_hidden: Include hidden/system files.

    Returns:
        {entries: [{name, type, size_bytes, modified}], path, count}
    """
    p = Path(path)
    if not p.exists():
        return {"error": f"Directory not found: {path}"}

    glob_fn = p.rglob if recursive else p.glob
    entries = []
    for item in sorted(glob_fn(pattern)):
        if not include_hidden and item.name.startswith("."):
            continue
        try:
            stat = item.stat()
            entries.append({
                "name": str(item.relative_to(p)) if recursive else item.name,
                "type": "dir" if item.is_dir() else "file",
                "size_bytes": stat.st_size if item.is_file() else None,
                "modified": datetime.fromtimestamp(stat.st_mtime).isoformat(),
            })
        except (PermissionError, OSError):
            pass

    return {"entries": entries, "path": str(p.resolve()), "count": len(entries)}


# ─────────────────────────────────────────────────────────────────────────────
# Tools — Process management
# ─────────────────────────────────────────────────────────────────────────────
@mcp.tool()
def process_list(filter_name: str = "") -> dict:
    """
    List running processes on the VM.

    Args:
        filter_name: Optional case-insensitive substring to filter by process name.

    Returns:
        {processes: [{pid, name, cpu_percent, memory_mb, status}], count}
    """
    try:
        import psutil
        procs = []
        for p in psutil.process_iter(["pid", "name", "cpu_percent", "memory_info", "status"]):
            try:
                info = p.info
                name = info.get("name", "")
                if filter_name and filter_name.lower() not in name.lower():
                    continue
                mem = info.get("memory_info")
                procs.append({
                    "pid": info["pid"],
                    "name": name,
                    "cpu_percent": info.get("cpu_percent", 0),
                    "memory_mb": round(mem.rss / 1_048_576, 1) if mem else None,
                    "status": info.get("status", ""),
                })
            except (psutil.NoSuchProcess, psutil.AccessDenied):
                pass
        procs.sort(key=lambda x: x["memory_mb"] or 0, reverse=True)
        return {"processes": procs, "count": len(procs)}
    except ImportError:
        # Fallback: use Get-Process
        result = _run_ps(
            "Get-Process | Select-Object Id,ProcessName,CPU,"
            "@{N='MemMB';E={[math]::Round($_.WorkingSet64/1MB,1)}} | "
            "ConvertTo-Json -Compress"
        )
        try:
            procs = json.loads(result["stdout"])
            if isinstance(procs, dict):
                procs = [procs]
            filtered = [
                {"pid": p.get("Id"), "name": p.get("ProcessName"), "memory_mb": p.get("MemMB")}
                for p in procs
                if not filter_name or filter_name.lower() in (p.get("ProcessName") or "").lower()
            ]
            return {"processes": filtered, "count": len(filtered)}
        except Exception:
            return {"raw": result["stdout"], "error": result["stderr"]}


@mcp.tool()
def process_kill(pid: int | None = None, name: str = "") -> dict:
    """
    Kill a process by PID or name.

    Args:
        pid: Process ID to kill (takes priority over name).
        name: Process name to kill (kills ALL matching processes).

    Returns:
        {killed, pids_killed}
    """
    if pid is not None:
        cmd = f"Stop-Process -Id {pid} -Force"
    elif name:
        cmd = f"Stop-Process -Name '{name}' -Force"
    else:
        return {"error": "Provide pid or name"}
    result = _run_ps(cmd)
    return {"killed": result["exit_code"] == 0, "stderr": result["stderr"]}


# ─────────────────────────────────────────────────────────────────────────────
# Tools — Screenshot
# ─────────────────────────────────────────────────────────────────────────────
@mcp.tool()
def screenshot(scale: float = 0.75) -> dict:
    """
    Capture the VM desktop as a PNG image.

    Args:
        scale: Resize factor (0.0–1.0, default 0.75 for smaller transfer).

    Returns:
        {image_base64, width, height, format}  — image_base64 is a PNG base64 string.
    """
    try:
        from PIL import ImageGrab, Image
        img = ImageGrab.grab()
        if scale != 1.0:
            w = int(img.width * scale)
            h = int(img.height * scale)
            img = img.resize((w, h), Image.LANCZOS)
        buf = io.BytesIO()
        img.save(buf, format="PNG", optimize=True)
        b64 = base64.b64encode(buf.getvalue()).decode("ascii")
        return {
            "image_base64": b64,
            "width": img.width,
            "height": img.height,
            "format": "PNG",
            "size_bytes": len(buf.getvalue()),
        }
    except ImportError:
        # Fallback: PowerShell + .NET
        ps = r"""
Add-Type -AssemblyName System.Windows.Forms
Add-Type -AssemblyName System.Drawing
$screen = [System.Windows.Forms.Screen]::PrimaryScreen.Bounds
$bmp = New-Object System.Drawing.Bitmap($screen.Width, $screen.Height)
$g = [System.Drawing.Graphics]::FromImage($bmp)
$g.CopyFromScreen($screen.Location, [System.Drawing.Point]::Empty, $screen.Size)
$g.Dispose()
$path = [System.IO.Path]::GetTempFileName() + ".png"
$bmp.Save($path, [System.Drawing.Imaging.ImageFormat]::Png)
$bmp.Dispose()
Write-Output $path
"""
        r = _run_ps(ps)
        img_path = r["stdout"].strip()
        if img_path and Path(img_path).exists():
            data = Path(img_path).read_bytes()
            Path(img_path).unlink(missing_ok=True)
            return {
                "image_base64": base64.b64encode(data).decode("ascii"),
                "format": "PNG",
                "size_bytes": len(data),
            }
        return {"error": "Screenshot failed", "stderr": r["stderr"]}


# ─────────────────────────────────────────────────────────────────────────────
# Tools — Clipboard
# ─────────────────────────────────────────────────────────────────────────────
@mcp.tool()
def clipboard_get() -> dict:
    """Return the current clipboard text from the VM."""
    r = _run_ps("Get-Clipboard")
    return {"text": r["stdout"].rstrip("\n"), "exit_code": r["exit_code"]}


@mcp.tool()
def clipboard_set(text: str) -> dict:
    """Set the VM clipboard to the given text."""
    escaped = text.replace("'", "''")
    r = _run_ps(f"Set-Clipboard -Value '{escaped}'")
    return {"success": r["exit_code"] == 0, "stderr": r["stderr"]}


# ─────────────────────────────────────────────────────────────────────────────
# Tools — Registry
# ─────────────────────────────────────────────────────────────────────────────
_REG_ROOTS = {
    "HKLM": winreg.HKEY_LOCAL_MACHINE,
    "HKCU": winreg.HKEY_CURRENT_USER,
    "HKCR": winreg.HKEY_CLASSES_ROOT,
    "HKU":  winreg.HKEY_USERS,
    "HKCC": winreg.HKEY_CURRENT_CONFIG,
}


def _split_reg_path(full_path: str):
    parts = full_path.replace("/", "\\").split("\\", 1)
    root_name = parts[0].upper()
    sub_key = parts[1] if len(parts) > 1 else ""
    root = _REG_ROOTS.get(root_name)
    if root is None:
        raise ValueError(f"Unknown registry root: {root_name!r}")
    return root, sub_key


@mcp.tool()
def registry_read(path: str, value_name: str = "") -> dict:
    """
    Read a registry value from the VM.

    Args:
        path: Registry path, e.g. HKLM\\SOFTWARE\\Nemo
        value_name: Value name to read. Empty string reads the default value.

    Returns:
        {data, type, path, value_name}
    """
    try:
        root, sub_key = _split_reg_path(path)
        with winreg.OpenKey(root, sub_key, access=winreg.KEY_READ | winreg.KEY_WOW64_64KEY) as key:
            data, reg_type = winreg.QueryValueEx(key, value_name)
        type_names = {
            winreg.REG_SZ: "REG_SZ", winreg.REG_DWORD: "REG_DWORD",
            winreg.REG_QWORD: "REG_QWORD", winreg.REG_BINARY: "REG_BINARY",
            winreg.REG_MULTI_SZ: "REG_MULTI_SZ", winreg.REG_EXPAND_SZ: "REG_EXPAND_SZ",
        }
        return {
            "data": data if not isinstance(data, bytes) else base64.b64encode(data).decode(),
            "type": type_names.get(reg_type, str(reg_type)),
            "path": path,
            "value_name": value_name or "(default)",
        }
    except FileNotFoundError:
        return {"error": f"Registry key/value not found: {path}\\{value_name}"}
    except Exception as e:
        return {"error": str(e)}


@mcp.tool()
def registry_write(path: str, value_name: str, data: Any, reg_type: str = "REG_SZ") -> dict:
    """
    Write a registry value on the VM.

    Args:
        path: Registry path, e.g. HKLM\\SOFTWARE\\Nemo
        value_name: Value name.
        data: Data to write.
        reg_type: Registry type: REG_SZ, REG_DWORD, REG_QWORD, REG_EXPAND_SZ, REG_BINARY.

    Returns:
        {success, path, value_name}
    """
    type_map = {
        "REG_SZ": winreg.REG_SZ,
        "REG_DWORD": winreg.REG_DWORD,
        "REG_QWORD": winreg.REG_QWORD,
        "REG_EXPAND_SZ": winreg.REG_EXPAND_SZ,
        "REG_BINARY": winreg.REG_BINARY,
        "REG_MULTI_SZ": winreg.REG_MULTI_SZ,
    }
    try:
        root, sub_key = _split_reg_path(path)
        wtype = type_map.get(reg_type.upper(), winreg.REG_SZ)
        if reg_type == "REG_DWORD":
            data = int(data)
        elif reg_type == "REG_BINARY":
            data = base64.b64decode(data)
        with winreg.CreateKeyEx(root, sub_key, access=winreg.KEY_WRITE | winreg.KEY_WOW64_64KEY) as key:
            winreg.SetValueEx(key, value_name, 0, wtype, data)
        return {"success": True, "path": path, "value_name": value_name}
    except Exception as e:
        return {"error": str(e)}


# ─────────────────────────────────────────────────────────────────────────────
# Tools — Windows Services
# ─────────────────────────────────────────────────────────────────────────────
@mcp.tool()
def service_status(name: str) -> dict:
    """
    Get the status of a Windows service.

    Args:
        name: Service name (e.g. NemoSvc, wuauserv).

    Returns:
        {name, display_name, status, start_type, pid}
    """
    r = _run_ps(
        f"Get-Service -Name '{name}' -ErrorAction SilentlyContinue | "
        f"Select-Object Name,DisplayName,Status,StartType | ConvertTo-Json -Compress"
    )
    try:
        s = json.loads(r["stdout"])
        return s
    except Exception:
        return {"raw": r["stdout"], "error": r["stderr"]}


@mcp.tool()
def service_control(name: str, action: str) -> dict:
    """
    Start, stop, or restart a Windows service.

    Args:
        name: Service name.
        action: One of: start, stop, restart.

    Returns:
        {success, status, stderr}
    """
    action = action.lower()
    if action == "start":
        cmd = f"Start-Service -Name '{name}' -ErrorAction Stop"
    elif action == "stop":
        cmd = f"Stop-Service -Name '{name}' -Force -ErrorAction Stop"
    elif action == "restart":
        cmd = f"Restart-Service -Name '{name}' -Force -ErrorAction Stop"
    else:
        return {"error": f"Unknown action: {action!r}. Use start/stop/restart."}

    r = _run_ps(cmd)
    # Get new status
    status_r = _run_ps(
        f"(Get-Service -Name '{name}' -ErrorAction SilentlyContinue).Status"
    )
    return {
        "success": r["exit_code"] == 0,
        "status": status_r["stdout"].strip(),
        "stderr": r["stderr"].strip(),
    }


# ─────────────────────────────────────────────────────────────────────────────
# Tools — Software install / download
# ─────────────────────────────────────────────────────────────────────────────
@mcp.tool()
def download_file(url: str, destination: str, timeout_seconds: int = 300) -> dict:
    """
    Download a file from the internet to the VM filesystem.

    Args:
        url: URL to download.
        destination: Local path to save the file.
        timeout_seconds: Download timeout (default 300 s).

    Returns:
        {path, size_bytes, success}
    """
    dest = Path(destination)
    dest.parent.mkdir(parents=True, exist_ok=True)

    r = _run_ps(
        f"Invoke-WebRequest -Uri '{url}' -OutFile '{destination}' "
        f"-UseBasicParsing -TimeoutSec {timeout_seconds}",
        timeout=timeout_seconds + 10,
    )
    if r["exit_code"] != 0:
        return {"success": False, "stderr": r["stderr"]}

    size = dest.stat().st_size if dest.exists() else 0
    return {"success": True, "path": str(dest.resolve()), "size_bytes": size}


@mcp.tool()
def install_msi(
    source: str,
    silent_args: str = "/quiet /norestart",
    is_url: bool = False,
    timeout_seconds: int = 300,
) -> dict:
    """
    Install an MSI, EXE, or other installer silently on the VM.

    Args:
        source: Local path to the installer file, OR a URL if is_url=True.
        silent_args: Arguments passed to the installer (default /quiet /norestart).
        is_url: If True, download source URL to a temp file first.
        timeout_seconds: Install timeout (default 300 s).

    Returns:
        {success, exit_code, stdout, stderr}
    """
    local_path = source
    temp_file = None

    if is_url:
        ext = Path(source.split("?")[0]).suffix or ".exe"
        temp_file = Path(tempfile.mktemp(suffix=ext))
        dl = download_file(source, str(temp_file), timeout_seconds=120)
        if not dl.get("success"):
            return {"success": False, "error": "Download failed", **dl}
        local_path = str(temp_file)

    p = Path(local_path)
    if not p.exists():
        return {"success": False, "error": f"Installer not found: {local_path}"}

    ext = p.suffix.lower()
    if ext == ".msi":
        cmd = f'Start-Process msiexec.exe -ArgumentList "/i \\"{local_path}\\" {silent_args}" -Wait -PassThru | Select-Object -ExpandProperty ExitCode'
    else:
        cmd = f'Start-Process \\"{local_path}\\" -ArgumentList "{silent_args}" -Wait -PassThru | Select-Object -ExpandProperty ExitCode'

    r = _run_ps(cmd, timeout=timeout_seconds)

    if temp_file:
        temp_file.unlink(missing_ok=True)

    return {
        "success": r["exit_code"] == 0,
        "installer_exit_code": r["stdout"].strip(),
        "stderr": r["stderr"].strip(),
    }


# ─────────────────────────────────────────────────────────────────────────────
# Tools — VM system info
# ─────────────────────────────────────────────────────────────────────────────
@mcp.tool()
def vm_info() -> dict:
    """
    Return system information about the VM:
    hostname, OS version, IP addresses, RAM, disk space, uptime, Python version.
    """
    # Hostname + OS
    hostname = socket.gethostname()
    os_ver = platform.version()
    os_release = platform.release()

    # IPs
    try:
        ips = socket.getaddrinfo(hostname, None)
        ip_list = list({addr[4][0] for addr in ips if ":" not in addr[4][0]})
    except Exception:
        ip_list = []

    # Public IP (best-effort)
    public_ip = ""
    try:
        public_ip = httpx.get("https://api.ipify.org", timeout=5).text.strip()
    except Exception:
        pass

    # RAM
    r_ram = _run_ps(
        "(Get-CimInstance Win32_ComputerSystem).TotalPhysicalMemory"
    )
    total_ram_gb = ""
    try:
        total_ram_gb = round(int(r_ram["stdout"].strip()) / 1_073_741_824, 1)
    except Exception:
        pass

    # Disk
    r_disk = _run_ps(
        "Get-PSDrive C | Select-Object @{N='UsedGB';E={[math]::Round($_.Used/1GB,1)}},"
        "@{N='FreeGB';E={[math]::Round($_.Free/1GB,1)}} | ConvertTo-Json -Compress"
    )
    disk_info = {}
    try:
        disk_info = json.loads(r_disk["stdout"])
    except Exception:
        pass

    # Uptime
    r_up = _run_ps(
        "(Get-Date) - (gcim Win32_OperatingSystem).LastBootUpTime | "
        "Select-Object -ExpandProperty TotalHours"
    )
    uptime_hours = r_up["stdout"].strip()

    return {
        "hostname": hostname,
        "os": f"Windows {os_release} ({os_ver})",
        "architecture": platform.machine(),
        "python": sys.version,
        "local_ips": ip_list,
        "public_ip": public_ip,
        "ram_total_gb": total_ram_gb,
        "disk_c": disk_info,
        "uptime_hours": uptime_hours,
        "mcp_server_port": PORT,
        "timestamp": datetime.now().isoformat(),
    }


# ─────────────────────────────────────────────────────────────────────────────
# Tools — Reboot
# ─────────────────────────────────────────────────────────────────────────────
@mcp.tool()
def reboot(delay_seconds: int = 10, message: str = "VM MCP initiated reboot") -> dict:
    """
    Schedule a reboot of the VM.

    Args:
        delay_seconds: Seconds to wait before rebooting (default 10, max 3600).
        message: Message shown in the shutdown dialog (if applicable).

    Returns:
        {scheduled, delay_seconds, message}
    """
    delay_seconds = max(0, min(delay_seconds, 3600))
    cmd = f'shutdown.exe /r /t {delay_seconds} /c "{message}"'
    r = _run_ps(cmd)
    return {
        "scheduled": r["exit_code"] == 0,
        "delay_seconds": delay_seconds,
        "message": message,
        "stderr": r["stderr"].strip(),
    }


@mcp.tool()
def reboot_cancel() -> dict:
    """Cancel a pending shutdown/reboot scheduled by the reboot tool."""
    r = _run_ps("shutdown.exe /a")
    return {"cancelled": r["exit_code"] == 0, "stderr": r["stderr"].strip()}


# ─────────────────────────────────────────────────────────────────────────────
# Entry point
# ─────────────────────────────────────────────────────────────────────────────
def main():
    import uvicorn

    # Build the ASGI app from FastMCP and wrap with auth middleware
    app = mcp.http_app(transport="sse")
    from starlette.applications import Starlette
    from starlette.routing import Mount, Route
    from starlette.responses import PlainTextResponse

    # Wrap the MCP SSE app with our auth middleware
    from starlette.middleware import Middleware
    wrapped = Starlette(
        routes=[Mount("/", app=app)],
        middleware=[Middleware(AuthMiddleware)],
    )

    print(f"""
╔═══════════════════════════════════════════════════════╗
║          Nemo VM MCP Server  v1.0                     ║
╠═══════════════════════════════════════════════════════╣
║  Listening : {HOST}:{PORT:<40}║
║  SSE URL   : http://<your-ip>:{PORT}/sse              ║
║  Auth      : X-Api-Key header required                ║
║  Tools     : {len([t for t in dir(mcp) if not t.startswith('_')]):<40} ║
╚═══════════════════════════════════════════════════════╝

Add to Kiro mcp.json:
  "vm-mcp": {{
    "url": "http://<VM-PUBLIC-IP>:{PORT}/sse",
    "headers": {{"X-Api-Key": "<your-key>"}}
  }}
""")

    uvicorn.run(wrapped, host=HOST, port=PORT, log_level="info")


if __name__ == "__main__":
    main()
