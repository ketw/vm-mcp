"""
vm-mcp/src/server.py  —  Remote Windows VM Control MCP Server  v2.0
====================================================================

Runs on the target Windows VM. Exposes every tool over HTTP/SSE so
any MCP client (Kiro CLI, Claude Desktop, etc.) can connect from the
internet and fully remote-control the machine.

TOOLS (41 total)
────────────────
 Shell & execution
   shell_run              PowerShell command → stdout/stderr/exit_code (Session 0 / headless)
   shell_run_elevated     Same but as SYSTEM via scheduled-task trick
   shell_run_bg           Fire-and-forget background command (no wait)
   shell_run_interactive  Run in the interactive user desktop session (windows VISIBLE on screen)
   winget_install         Install software via winget package manager
   winget_search          Search winget catalogue
   winget_list            List installed packages

 File system
   file_read              Read file (text or base64 binary)
   file_write             Write / create file (text or base64 binary)
   file_delete            Delete file or directory
   file_list              List directory (recursive, glob pattern)
   file_copy              Copy file or directory tree
   file_move              Move / rename file or directory
   file_hash              SHA256 / MD5 hash of a file
   archive_create         Zip files/dirs
   archive_extract        Extract zip/tar/7z archive

 Processes
   process_list           List running processes (name, pid, cpu%, mem)
   process_start          Start a new process (interactive=True to show on desktop)
   process_kill           Kill process by PID or name

 UI & desktop
   screenshot             Desktop PNG → base64
   mouse_move             Move mouse to x,y
   mouse_click            Left/right/double click at x,y
   keyboard_type          Type a string (simulated keystrokes)
   keyboard_hotkey        Send a hotkey (e.g. ctrl+c, win+d)
   toast_notify           Windows toast notification

 AI Cursor & Input (Session-aware, purple overlay cursor)
   ai_cursor_start        Launch the purple AI cursor overlay in the active session
   ai_cursor_stop         Kill the AI cursor overlay
   ai_cursor_status       Check if the overlay is running
   ai_mouse_move          Move the AI cursor to x,y (SendInput — works in Session 1)
   ai_mouse_click         Click at x,y via SendInput
   ai_mouse_scroll        Scroll the mouse wheel at x,y
   ai_key_press           Press a single key or key combo via SendInput
   ai_type                Type a string via SendInput (Unicode aware)
   ai_screenshot          Screenshot with the purple cursor drawn at its current position

 Clipboard
   clipboard_get          Read clipboard text
   clipboard_set          Write clipboard text

 Registry
   registry_read          Read a value
   registry_write         Write a value
   registry_delete        Delete a value or key
   registry_list          List subkeys and values in a key

 Windows services
   service_status         Get service status
   service_control        Start / stop / restart
   service_list           List all services

 Networking
   net_info               IP addresses, routes, DNS, public IP
   net_ping               Ping a host
   net_port_scan          TCP port scan on a host
   net_connections        Active TCP/UDP connections
   firewall_rule_list     List Windows Firewall rules
   firewall_rule_add      Add an inbound/outbound firewall rule
   firewall_rule_remove   Remove a firewall rule by name

 System
   vm_info                OS, RAM, CPU, disk, uptime
   event_log_read         Read Windows Event Log entries
   scheduled_task_list    List scheduled tasks
   scheduled_task_run     Run a scheduled task immediately
   env_get                Read an environment variable
   env_set                Set/delete an environment variable (Machine scope)
   installed_drivers      List installed kernel drivers

 Software & packages
   download_file          Download URL to local path
   install_msi            Install MSI/EXE silently (URL or local)

 Server self-management
   server_update          Fetch latest server.py from GitHub and hot-reload
   server_version         Return current server version and git commit
   server_restart         Restart the server process (applies update)
   server_log_tail        Return last N lines of the server log file

 Power
   reboot                 Reboot the VM
   reboot_cancel          Cancel pending reboot
   shutdown               Shut down the VM

Security
────────
  X-Api-Key header required on every request (set via VM_MCP_API_KEY env var).
  Optional IP allowlist: VM_MCP_ALLOWED_IPS=1.2.3.4,10.0.0.0/8
  Optional rate limiting: VM_MCP_RATE_LIMIT=60  (requests/minute, 0=off)

Self-update
───────────
  server_update() fetches src/server.py from the GitHub repo (default:
  https://raw.githubusercontent.com/ketw/vm-mcp/master/src/server.py),
  writes it to the install dir, then server_restart() re-execs the process.
  The whole cycle can be triggered in one call: server_update(restart=True).
"""

from __future__ import annotations

import base64
import ctypes
import hashlib
import io
import ipaddress
import json
import logging
import os
import platform
import shutil
import signal
import socket
import subprocess
import sys
import tempfile
import threading
import time
import winreg
import zipfile
from datetime import datetime
from pathlib import Path
from typing import Any, Optional

# Fix stdout encoding for Windows consoles that default to cp1252
if sys.platform == "win32":
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

import httpx
from fastmcp import FastMCP
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import JSONResponse, PlainTextResponse

# ─────────────────────────────────────────────────────────────────────────────
# Version
# ─────────────────────────────────────────────────────────────────────────────
SERVER_VERSION = "2.3.1"
SERVER_FILE    = Path(__file__).resolve()
INSTALL_DIR    = SERVER_FILE.parent

# ─────────────────────────────────────────────────────────────────────────────
# Config from environment
# ─────────────────────────────────────────────────────────────────────────────
API_KEY: str = os.environ.get("VM_MCP_API_KEY", "")
if not API_KEY:
    sys.exit(
        "ERROR: VM_MCP_API_KEY is not set.\n"
        "Set it before starting:  $env:VM_MCP_API_KEY = 'your-secret-key'\n"
    )

PORT:         int = int(os.environ.get("VM_MCP_PORT", "8765"))
HOST:         str = os.environ.get("VM_MCP_HOST", "0.0.0.0")
SHELL_TIMEOUT:int = int(os.environ.get("VM_MCP_SHELL_TIMEOUT", "120"))
RATE_LIMIT:   int = int(os.environ.get("VM_MCP_RATE_LIMIT", "0"))   # req/min, 0=off
UPDATE_URL:   str = os.environ.get(
    "VM_MCP_UPDATE_URL",
    "https://raw.githubusercontent.com/ketw/vm-mcp/master/src/server.py",
)

# Full paths — required when running as SYSTEM (which has a minimal PATH)
PWSH:   str = r"C:\Windows\System32\WindowsPowerShell\v1.0\powershell.exe"
CMD:    str = r"C:\Windows\System32\cmd.exe"
SCHTASKS: str = r"C:\Windows\System32\schtasks.exe"
WINGET: str = ""  # Resolved dynamically — WindowsApps alias can't be used by SYSTEM

def _winget_path() -> str:
    """Find the real winget.exe path (not the WindowsApps Store alias)."""
    global WINGET
    if WINGET and Path(WINGET).exists():
        return WINGET
    # Try to resolve via Get-AppxPackage
    r = _run_ps(
        "(Get-AppxPackage Microsoft.DesktopAppInstaller -ErrorAction SilentlyContinue).InstallLocation",
        timeout=10,
    )
    install_loc = r["stdout"].strip()
    if install_loc:
        candidate = Path(install_loc) / "winget.exe"
        if candidate.exists():
            WINGET = str(candidate)
            return WINGET
    # Fallback: search common locations
    for p in [
        r"C:\Users\User0\AppData\Local\Microsoft\WindowsApps\winget.exe",
        r"C:\Program Files\WindowsApps\Microsoft.DesktopAppInstaller_1.0.0.0_x64__8wekyb3d8bbwe\winget.exe",
    ]:
        if Path(p).exists():
            WINGET = p
            return WINGET
    return ""
LOG_FILE: Path = Path(os.environ.get("VM_MCP_LOG", str(INSTALL_DIR / "vm-mcp.log")))

# IP allowlist
ALLOWED_NETWORKS: list = []
_raw_ips = os.environ.get("VM_MCP_ALLOWED_IPS", "")
if _raw_ips:
    for _e in _raw_ips.split(","):
        _e = _e.strip()
        if _e:
            try:
                ALLOWED_NETWORKS.append(ipaddress.ip_network(_e, strict=False))
            except ValueError:
                print(f"[vm-mcp] WARN: bad IP/CIDR in allowlist: {_e!r}")

# ─────────────────────────────────────────────────────────────────────────────
# Logging
# ─────────────────────────────────────────────────────────────────────────────
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [vm-mcp] %(levelname)s %(message)s",
    handlers=[
        logging.FileHandler(LOG_FILE, encoding="utf-8"),
        logging.StreamHandler(sys.stdout),
    ],
)
log = logging.getLogger("vm-mcp")

# ─────────────────────────────────────────────────────────────────────────────
# Rate limiter (token bucket per IP, optional)
# ─────────────────────────────────────────────────────────────────────────────
_rate_buckets: dict[str, list[float]] = {}
_rate_lock = threading.Lock()

def _check_rate_limit(client_ip: str) -> bool:
    """Return True if the request is allowed."""
    if RATE_LIMIT <= 0:
        return True
    now = time.monotonic()
    window = 60.0
    with _rate_lock:
        timestamps = _rate_buckets.get(client_ip, [])
        timestamps = [t for t in timestamps if now - t < window]
        if len(timestamps) >= RATE_LIMIT:
            _rate_buckets[client_ip] = timestamps
            return False
        timestamps.append(now)
        _rate_buckets[client_ip] = timestamps
    return True

# ─────────────────────────────────────────────────────────────────────────────
# MCP server
# ─────────────────────────────────────────────────────────────────────────────
mcp = FastMCP(
    name="vm-mcp",
    instructions=(
        "Remote Windows VM control — 48 tools for shell, files, processes, UI, "
        "registry, services, networking, AI cursor/input, and self-update. "
        "API key required in X-Api-Key header."
    ),
)

# ─────────────────────────────────────────────────────────────────────────────
# Auth + rate-limit middleware
# ─────────────────────────────────────────────────────────────────────────────
class AuthMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        path = request.url.path
        if path in ("/health", "/ping", "/version"):
            return PlainTextResponse(f"vm-mcp {SERVER_VERSION} ok")

        # IP allowlist
        if ALLOWED_NETWORKS:
            client_ip_str = request.client.host if request.client else ""
            try:
                cip = ipaddress.ip_address(client_ip_str)
                if not any(cip in net for net in ALLOWED_NETWORKS):
                    log.warning("Blocked IP: %s", client_ip_str)
                    return JSONResponse({"error": "IP not in allowlist"}, status_code=403)
            except ValueError:
                return JSONResponse({"error": "Cannot parse client IP"}, status_code=403)

        # Rate limit
        client_ip = request.client.host if request.client else "unknown"
        if not _check_rate_limit(client_ip):
            log.warning("Rate limit hit: %s", client_ip)
            return JSONResponse({"error": "Rate limit exceeded"}, status_code=429)

        # API key
        key = request.headers.get("X-Api-Key", "")
        if key != API_KEY:
            log.warning("Bad API key from %s", client_ip)
            return JSONResponse({"error": "Unauthorized"}, status_code=401)

        log.info("%s %s", request.method, path)
        return await call_next(request)

# ─────────────────────────────────────────────────────────────────────────────
# Internal helpers
# ─────────────────────────────────────────────────────────────────────────────
def _run_ps(command: str, timeout: int = SHELL_TIMEOUT, elevated: bool = False) -> dict:
    """Execute a PowerShell command; return {stdout, stderr, exit_code}."""
    if elevated:
        task = f"VmMcp_{datetime.now().strftime('%H%M%S%f')}"
        script = Path(tempfile.mktemp(suffix=".ps1"))
        out    = Path(tempfile.mktemp(suffix=".txt"))
        script.write_text(
            f"$ErrorActionPreference='Continue'\n[Console]::OutputEncoding=[System.Text.Encoding]::UTF8\n{command}\n"
            f'$LASTEXITCODE | Out-File "{out}" -Encoding utf8 -Append',
            encoding="utf-8-sig",
        )
        subprocess.run(
            f'"{SCHTASKS}" /Create /TN "{task}" /TR '
            f'"{PWSH} -ExecutionPolicy Bypass -NonInteractive -File \\"{script}\\"" '
            f"/SC ONCE /ST 00:00 /RU SYSTEM /F",
            shell=True, capture_output=True,
        )
        subprocess.run(f'"{SCHTASKS}" /Run /TN "{task}"', shell=True, capture_output=True)
        time.sleep(min(timeout, 60))
        subprocess.run(f'"{SCHTASKS}" /Delete /TN "{task}" /F', shell=True, capture_output=True)
        stdout = out.read_text(encoding="utf-8-sig", errors="replace") if out.exists() else ""
        script.unlink(missing_ok=True); out.unlink(missing_ok=True)
        return {"stdout": stdout.rstrip("\n"), "stderr": "", "exit_code": 0}

    result = subprocess.run(
        [PWSH, "-ExecutionPolicy", "Bypass", "-NonInteractive", "-Command", command],
        capture_output=True, text=True, timeout=timeout,
        encoding="utf-8", errors="replace",
    )
    return {
        "stdout":    result.stdout.rstrip("\n"),
        "stderr":    result.stderr.rstrip("\n"),
        "exit_code": result.returncode,
    }

def _get_interactive_session_id() -> int:
    """Return the active console session ID (the one shown on the physical display)."""
    wts = ctypes.WinDLL("wtsapi32.dll", use_last_error=True)
    kernel32 = ctypes.WinDLL("kernel32.dll", use_last_error=True)
    # WTSGetActiveConsoleSessionId returns the session attached to the physical console
    kernel32.WTSGetActiveConsoleSessionId.restype = ctypes.c_uint32
    return kernel32.WTSGetActiveConsoleSessionId()


def _launch_in_session(exe: str, args: str = "", session_id: Optional[int] = None) -> dict:
    """
    Launch a process directly into the interactive user desktop session using
    WTSQueryUserToken + DuplicateTokenEx + CreateProcessAsUser via ctypes.

    This is the correct way to show windows on the physical display from a
    Session-0 service. No schtasks, no PowerShell wrapper — the process
    appears directly in the user's desktop session.

    Returns: {started, pid, error}
    """
    import ctypes.wintypes

    wtsapi32  = ctypes.WinDLL("wtsapi32.dll",  use_last_error=True)
    advapi32  = ctypes.WinDLL("advapi32.dll",  use_last_error=True)
    kernel32  = ctypes.WinDLL("kernel32.dll",  use_last_error=True)
    userenv   = ctypes.WinDLL("userenv.dll",   use_last_error=True)

    # --- determine session ---
    if session_id is None:
        session_id = kernel32.WTSGetActiveConsoleSessionId()

    # --- WTSQueryUserToken ---
    user_token = ctypes.wintypes.HANDLE()
    if not wtsapi32.WTSQueryUserToken(ctypes.c_uint32(session_id), ctypes.byref(user_token)):
        err = ctypes.get_last_error()
        return {"started": False, "pid": None, "error": f"WTSQueryUserToken failed: {err}"}

    # --- DuplicateTokenEx (PRIMARY token, full access) ---
    TOKEN_ALL_ACCESS = 0xF01FF
    SecurityImpersonation = 2
    TokenPrimary = 1
    dup_token = ctypes.wintypes.HANDLE()
    ok = advapi32.DuplicateTokenEx(
        user_token, TOKEN_ALL_ACCESS, None,
        SecurityImpersonation, TokenPrimary,
        ctypes.byref(dup_token),
    )
    kernel32.CloseHandle(user_token)
    if not ok:
        err = ctypes.get_last_error()
        return {"started": False, "pid": None, "error": f"DuplicateTokenEx failed: {err}"}

    # --- CreateEnvironmentBlock ---
    env_block = ctypes.c_void_p()
    userenv.CreateEnvironmentBlock(ctypes.byref(env_block), dup_token, False)

    # --- STARTUPINFOW ---
    class STARTUPINFOW(ctypes.Structure):
        _fields_ = [
            ("cb",              ctypes.c_uint32),
            ("lpReserved",      ctypes.c_wchar_p),
            ("lpDesktop",       ctypes.c_wchar_p),
            ("lpTitle",         ctypes.c_wchar_p),
            ("dwX",             ctypes.c_uint32),
            ("dwY",             ctypes.c_uint32),
            ("dwXSize",         ctypes.c_uint32),
            ("dwYSize",         ctypes.c_uint32),
            ("dwXCountChars",   ctypes.c_uint32),
            ("dwYCountChars",   ctypes.c_uint32),
            ("dwFillAttribute", ctypes.c_uint32),
            ("dwFlags",         ctypes.c_uint32),
            ("wShowWindow",     ctypes.c_uint16),
            ("cbReserved2",     ctypes.c_uint16),
            ("lpReserved2",     ctypes.c_void_p),
            ("hStdInput",       ctypes.wintypes.HANDLE),
            ("hStdOutput",      ctypes.wintypes.HANDLE),
            ("hStdError",       ctypes.wintypes.HANDLE),
        ]

    class PROCESS_INFORMATION(ctypes.Structure):
        _fields_ = [
            ("hProcess",    ctypes.wintypes.HANDLE),
            ("hThread",     ctypes.wintypes.HANDLE),
            ("dwProcessId", ctypes.c_uint32),
            ("dwThreadId",  ctypes.c_uint32),
        ]

    si = STARTUPINFOW()
    si.cb        = ctypes.sizeof(si)
    si.lpDesktop = "winsta0\\default"
    pi = PROCESS_INFORMATION()

    # CREATE_UNICODE_ENVIRONMENT = 0x400, NORMAL_PRIORITY_CLASS = 0x20
    CREATE_FLAGS = 0x00000420

    cmd_line = f'"{exe}"' if not args else f'"{exe}" {args}'

    ok = advapi32.CreateProcessAsUserW(
        dup_token,
        exe,
        cmd_line,
        None, None, False,
        CREATE_FLAGS,
        env_block,
        None,
        ctypes.byref(si),
        ctypes.byref(pi),
    )

    last_err = ctypes.get_last_error()
    userenv.DestroyEnvironmentBlock(env_block)
    kernel32.CloseHandle(dup_token)

    if ok:
        pid = pi.dwProcessId
        kernel32.CloseHandle(pi.hProcess)
        kernel32.CloseHandle(pi.hThread)
        return {"started": True, "pid": pid, "error": None}
    else:
        return {"started": False, "pid": None, "error": f"CreateProcessAsUserW failed: {last_err}"}


def _run_in_user_session(
    command: str,
    timeout: int = 30,
    capture_output: bool = True,
    session_id: Optional[int] = None,
) -> dict:
    """
    Run a PowerShell command in the interactive user desktop session.

    For fire-and-forget GUI launches (capture_output=False) this calls
    _launch_in_session directly — no schtasks, no wrapper process.

    For captured output, it writes a temp .ps1, launches it via
    _launch_in_session, waits, and reads the output file.

    Returns: {stdout, stderr, exit_code}
    """
    if not capture_output:
        # Fire-and-forget: launch PowerShell with the command directly
        result = _launch_in_session(PWSH,
            f'-ExecutionPolicy Bypass -NonInteractive -WindowStyle Hidden -Command {command}',
            session_id=session_id)
        return {"stdout": "", "stderr": result.get("error") or "", "exit_code": 0 if result["started"] else 1}

    # Captured: write script, launch it, wait, read output
    out    = Path(tempfile.mktemp(suffix=".txt"))
    script = Path(tempfile.mktemp(suffix=".ps1"))
    script_body = (
        "$ErrorActionPreference='Continue'\n"
        "[Console]::OutputEncoding=[System.Text.Encoding]::UTF8\n"
        f"$_vmout = (& {{{command}}} 2>&1 | Out-String)\n"
        f'$_vmout | Out-File -FilePath "{out}" -Encoding utf8\n'
    )
    script.write_text(script_body, encoding="utf-8-sig")
    try:
        result = _launch_in_session(
            PWSH,
            f'-ExecutionPolicy Bypass -NonInteractive -WindowStyle Hidden -File "{script}"',
            session_id=session_id,
        )
        if not result["started"]:
            return {"stdout": "", "stderr": result.get("error", "launch failed"), "exit_code": 1}
        time.sleep(min(timeout, 60))
        stdout = out.read_text(encoding="utf-8-sig", errors="replace").rstrip("\n") if out.exists() else ""
    finally:
        script.unlink(missing_ok=True)
        out.unlink(missing_ok=True)

    return {"stdout": stdout, "stderr": "", "exit_code": 0}


def _pyautogui():
    """Import pyautogui lazily; raise ImportError with install hint if missing."""
    try:
        import pyautogui
        pyautogui.FAILSAFE = False
        return pyautogui
    except ImportError:
        raise ImportError("pyautogui not installed. Run: pip install pyautogui")

_REG_ROOTS = {
    "HKLM": winreg.HKEY_LOCAL_MACHINE,
    "HKCU": winreg.HKEY_CURRENT_USER,
    "HKCR": winreg.HKEY_CLASSES_ROOT,
    "HKU":  winreg.HKEY_USERS,
    "HKCC": winreg.HKEY_CURRENT_CONFIG,
}
_REG_TYPE_MAP = {
    "REG_SZ":        winreg.REG_SZ,
    "REG_DWORD":     winreg.REG_DWORD,
    "REG_QWORD":     winreg.REG_QWORD,
    "REG_EXPAND_SZ": winreg.REG_EXPAND_SZ,
    "REG_BINARY":    winreg.REG_BINARY,
    "REG_MULTI_SZ":  winreg.REG_MULTI_SZ,
}
_REG_TYPE_NAMES = {v: k for k, v in _REG_TYPE_MAP.items()}

def _split_reg(full_path: str):
    parts = full_path.replace("/", "\\").split("\\", 1)
    root  = _REG_ROOTS.get(parts[0].upper())
    if root is None:
        raise ValueError(f"Unknown registry root: {parts[0]!r}")
    return root, parts[1] if len(parts) > 1 else ""


# ═════════════════════════════════════════════════════════════════════════════
# SECTION 1 — Shell & execution
# ═════════════════════════════════════════════════════════════════════════════

@mcp.tool()
def shell_run(
    command: str,
    timeout_seconds: int = SHELL_TIMEOUT,
    working_directory: str = "",
) -> dict:
    """
    Run a PowerShell command and return the result.

    Args:
        command: PowerShell command or script block.
        timeout_seconds: Max execution time (default 120, max 600).
        working_directory: Optional directory to cd into first.

    Returns: {stdout, stderr, exit_code}
    """
    timeout_seconds = min(timeout_seconds, 600)
    if working_directory:
        command = f'Set-Location -Path "{working_directory}"; {command}'
    result = subprocess.run(
        [PWSH, "-ExecutionPolicy", "Bypass", "-NonInteractive", "-Command", command],
        capture_output=True, text=True, timeout=timeout_seconds,
        encoding="utf-8", errors="replace",
    )
    return {
        "stdout":    result.stdout.rstrip("\n"),
        "stderr":    result.stderr.rstrip("\n"),
        "exit_code": result.returncode,
    }


@mcp.tool()
def shell_run_elevated(command: str, timeout_seconds: int = 60) -> dict:
    """
    Run a PowerShell command as SYSTEM via scheduled task.
    Use for driver installs, bcdedit, kernel-level operations.

    Args:
        command: PowerShell to run as SYSTEM.
        timeout_seconds: Max wait time (max 120 s).

    Returns: {stdout, stderr, exit_code}
    """
    return _run_ps(command, timeout=min(timeout_seconds, 120), elevated=True)


@mcp.tool()
def shell_run_bg(command: str, working_directory: str = "") -> dict:
    """
    Fire-and-forget: start a process in the background without waiting.
    Useful for launching GUI apps, long-running scripts, etc.

    Args:
        command: Command to run (passed to cmd.exe /c).
        working_directory: Optional working directory.

    Returns: {started, pid}
    """
    kwargs: dict = {"shell": False}
    if working_directory:
        kwargs["cwd"] = working_directory
    proc = subprocess.Popen(
        [PWSH, "-ExecutionPolicy", "Bypass", "-NonInteractive", "-Command", command],
        **kwargs,
    )
    return {"started": True, "pid": proc.pid}


@mcp.tool()
def shell_run_interactive(
    command: str,
    timeout_seconds: int = 30,
    capture_output: bool = True,
) -> dict:
    """
    Run a PowerShell command in the INTERACTIVE user desktop session.

    Unlike shell_run (which runs in the service's Session 0 and cannot show
    windows), this bridges the Session-0 boundary via CreateProcessAsUser,
    targeting the active console session directly.  Any GUI launched by the
    command will be VISIBLE on the physical display.

    Use shell_run for headless/background work.
    Use shell_run_interactive when you need windows to actually appear, or when
    the command requires access to the interactive desktop (e.g. UI automation).

    Args:
        command:         PowerShell command or script block.
        timeout_seconds: How long to wait for output (max 120 s). Only used when capture_output=True.
        capture_output:  Return stdout (True, default) or fire-and-forget (False).

    Returns: {stdout, stderr, exit_code}
    """
    return _run_in_user_session(
        command,
        timeout=min(timeout_seconds, 120),
        capture_output=capture_output,
    )


@mcp.tool()
def winget_install(
    package_id: str,
    silent: bool = True,
    accept_agreements: bool = True,
) -> dict:
    """
    Install a package via winget (Windows Package Manager).

    Args:
        package_id: Winget package ID (e.g. 'Ngrok.Ngrok', 'Python.Python.3.12').
        silent: Pass --silent flag (default True).
        accept_agreements: Auto-accept license and source agreements (default True).

    Returns: {stdout, stderr, exit_code}
    """
    flags = ""
    if silent:          flags += " --silent"
    if accept_agreements: flags += " --accept-package-agreements --accept-source-agreements"
    wg = _winget_path()
    if not wg:
        return {"error": "winget.exe not found. Install App Installer from Microsoft Store.", "stdout": "", "exit_code": 1}
    return _run_ps(f"& '{wg}' install --id {package_id}{flags}", timeout=300)


@mcp.tool()
def winget_search(query: str, limit: int = 20) -> dict:
    """
    Search the winget catalogue.

    Args:
        query: Search term.
        limit: Max results to return (default 20).

    Returns: {results: [{id, name, version, source}], count}
    """
    wg = _winget_path()
    if not wg:
        return {"error": "winget.exe not found", "query": query}
    r = _run_ps(f"& '{wg}' search '{query}' --accept-source-agreements 2>&1")
    lines = [l for l in r["stdout"].splitlines() if l.strip() and "---" not in l]
    return {"raw": "\n".join(lines[:limit + 5]), "query": query}


@mcp.tool()
def winget_list(filter_name: str = "") -> dict:
    """
    List installed packages known to winget.

    Args:
        filter_name: Optional substring filter on package name.

    Returns: {raw output}
    """
    wg = _winget_path()
    if not wg:
        return {"error": "winget.exe not found", "stdout": "", "exit_code": 1}
    cmd = f"& '{wg}' list --accept-source-agreements"
    if filter_name:
        cmd += f" --name '{filter_name}'"
    return _run_ps(cmd, timeout=60)


# ═════════════════════════════════════════════════════════════════════════════
# SECTION 2 — File system
# ═════════════════════════════════════════════════════════════════════════════

@mcp.tool()
def file_read(path: str, encoding: str = "utf-8", as_base64: bool = False) -> dict:
    """
    Read a file from the VM.

    Args:
        path: File path.
        encoding: Text encoding (default utf-8). Ignored if as_base64=True.
        as_base64: Return raw bytes as base64 (for binaries/images).

    Returns: {content, size_bytes, path}
    """
    p = Path(path)
    if not p.exists():
        return {"error": f"Not found: {path}"}
    size = p.stat().st_size
    content = (
        base64.b64encode(p.read_bytes()).decode("ascii")
        if as_base64
        else p.read_text(encoding=encoding, errors="replace")
    )
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
    Write / overwrite a file on the VM.

    Args:
        path: Destination path.
        content: Text content, or base64 bytes if from_base64=True.
        encoding: Text encoding (default utf-8).
        from_base64: Decode content from base64 before writing.
        create_dirs: Create parent directories if needed.

    Returns: {path, size_bytes}
    """
    p = Path(path)
    if create_dirs:
        p.parent.mkdir(parents=True, exist_ok=True)
    if from_base64:
        p.write_bytes(base64.b64decode(content))
    else:
        p.write_text(content, encoding=encoding)
    return {"path": str(p.resolve()), "size_bytes": p.stat().st_size}


@mcp.tool()
def file_delete(path: str, recursive: bool = False) -> dict:
    """Delete a file or directory."""
    p = Path(path)
    if not p.exists():
        return {"error": f"Not found: {path}"}
    if p.is_dir():
        if not recursive:
            return {"error": "Is a directory — set recursive=True"}
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
    List directory contents.

    Args:
        path: Directory path (default current dir).
        pattern: Glob pattern (default *).
        recursive: Recurse subdirectories.
        include_hidden: Include dotfiles.

    Returns: {entries, path, count}
    """
    p = Path(path)
    if not p.exists():
        return {"error": f"Not found: {path}"}
    entries = []
    fn = p.rglob if recursive else p.glob
    for item in sorted(fn(pattern)):
        if not include_hidden and item.name.startswith("."):
            continue
        try:
            st = item.stat()
            entries.append({
                "name":       str(item.relative_to(p)) if recursive else item.name,
                "type":       "dir" if item.is_dir() else "file",
                "size_bytes": st.st_size if item.is_file() else None,
                "modified":   datetime.fromtimestamp(st.st_mtime).isoformat(),
            })
        except (PermissionError, OSError):
            pass
    return {"entries": entries, "path": str(p.resolve()), "count": len(entries)}


@mcp.tool()
def file_copy(source: str, destination: str, overwrite: bool = True) -> dict:
    """
    Copy a file or directory tree.

    Args:
        source: Source path.
        destination: Destination path.
        overwrite: Overwrite existing destination (default True).

    Returns: {copied, destination}
    """
    src = Path(source)
    dst = Path(destination)
    if not src.exists():
        return {"error": f"Source not found: {source}"}
    if src.is_dir():
        if dst.exists() and overwrite:
            shutil.rmtree(dst)
        shutil.copytree(src, dst)
    else:
        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(src, dst)
    return {"copied": True, "destination": str(dst.resolve())}


@mcp.tool()
def file_move(source: str, destination: str) -> dict:
    """Move or rename a file or directory."""
    src = Path(source)
    dst = Path(destination)
    if not src.exists():
        return {"error": f"Source not found: {source}"}
    dst.parent.mkdir(parents=True, exist_ok=True)
    shutil.move(str(src), dst)
    return {"moved": True, "source": str(src), "destination": str(dst.resolve())}


@mcp.tool()
def file_hash(path: str, algorithm: str = "sha256") -> dict:
    """
    Compute a cryptographic hash of a file.

    Args:
        path: File path.
        algorithm: Hash algorithm — sha256 (default), md5, sha1.

    Returns: {hash, algorithm, path, size_bytes}
    """
    p = Path(path)
    if not p.exists():
        return {"error": f"Not found: {path}"}
    algo = algorithm.lower()
    h = hashlib.new(algo)
    with open(p, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return {"hash": h.hexdigest(), "algorithm": algo, "path": str(p), "size_bytes": p.stat().st_size}


@mcp.tool()
def archive_create(
    output_path: str,
    sources: list[str],
    format: str = "zip",
) -> dict:
    """
    Create a zip archive from files or directories.

    Args:
        output_path: Destination archive path (e.g. C:\\Temp\\out.zip).
        sources: List of file/directory paths to include.
        format: Archive format — zip (default) or tar.

    Returns: {path, size_bytes, files_added}
    """
    out = Path(output_path)
    out.parent.mkdir(parents=True, exist_ok=True)
    files_added = 0
    if format == "zip":
        with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as zf:
            for src in sources:
                s = Path(src)
                if s.is_dir():
                    for f in s.rglob("*"):
                        if f.is_file():
                            zf.write(f, f.relative_to(s.parent))
                            files_added += 1
                elif s.exists():
                    zf.write(s, s.name)
                    files_added += 1
    elif format == "tar":
        import tarfile
        with tarfile.open(out, "w:gz") as tf:
            for src in sources:
                tf.add(src, arcname=Path(src).name)
                files_added += 1
    else:
        return {"error": f"Unsupported format: {format}. Use zip or tar."}
    return {"path": str(out.resolve()), "size_bytes": out.stat().st_size, "files_added": files_added}


@mcp.tool()
def archive_extract(
    archive_path: str,
    destination: str,
    overwrite: bool = True,
) -> dict:
    """
    Extract a zip, tar.gz, or 7z archive.

    Args:
        archive_path: Path to the archive.
        destination: Directory to extract into.
        overwrite: Overwrite existing files (default True).

    Returns: {extracted_to, files_extracted}
    """
    arc = Path(archive_path)
    dst = Path(destination)
    dst.mkdir(parents=True, exist_ok=True)
    if not arc.exists():
        return {"error": f"Archive not found: {archive_path}"}
    name = arc.name.lower()
    if name.endswith(".zip"):
        with zipfile.ZipFile(arc) as zf:
            count = len(zf.namelist())
            zf.extractall(dst)
    elif name.endswith((".tar.gz", ".tgz", ".tar.bz2", ".tar")):
        import tarfile
        with tarfile.open(arc) as tf:
            count = len(tf.getmembers())
            tf.extractall(dst)
    elif name.endswith(".7z"):
        # Try via PowerShell 7-zip if installed
        r = _run_ps(f'7z x "{arc}" -o"{dst}" -y')
        return {"extracted_to": str(dst), "exit_code": r["exit_code"], "stderr": r["stderr"]}
    else:
        # Generic fallback: Expand-Archive (zip only)
        r = _run_ps(f'Expand-Archive -Path "{arc}" -DestinationPath "{dst}" -Force')
        return {"extracted_to": str(dst), "exit_code": r["exit_code"]}
    return {"extracted_to": str(dst.resolve()), "files_extracted": count}


# ═════════════════════════════════════════════════════════════════════════════
# SECTION 3 — Processes
# ═════════════════════════════════════════════════════════════════════════════

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
        r = _run_ps(
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
        result = _launch_in_session(executable, arguments or "")
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
    r = _run_ps(cmd)
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


# ═════════════════════════════════════════════════════════════════════════════
# SECTION 4 — UI & desktop
# ═════════════════════════════════════════════════════════════════════════════

@mcp.tool()
def screenshot(scale: float = 0.75, region: Optional[list[int]] = None) -> dict:
    """
    Capture the VM desktop as a base64-encoded PNG.

    Args:
        scale: Resize factor 0.1–1.0 (default 0.75).
        region: Optional [x, y, width, height] to capture a region only.

    Returns: {image_base64, width, height, size_bytes}
    """
    # Primary: GDI screenshot via PowerShell — output as base64 to avoid temp file lock
    ps_script = r"""
Add-Type -AssemblyName System.Windows.Forms,System.Drawing
$screens = [System.Windows.Forms.Screen]::AllScreens
$bounds  = [System.Drawing.Rectangle]::Empty
foreach ($s in $screens) { $bounds = [System.Drawing.Rectangle]::Union($bounds, $s.Bounds) }
$bmp = New-Object System.Drawing.Bitmap($bounds.Width, $bounds.Height)
$g   = [System.Drawing.Graphics]::FromImage($bmp)
$g.CopyFromScreen($bounds.Location, [System.Drawing.Point]::Empty, $bounds.Size)
$g.Dispose()
$ms = New-Object System.IO.MemoryStream
$bmp.Save($ms, [System.Drawing.Imaging.ImageFormat]::Png)
$bmp.Dispose()
$bytes = $ms.ToArray()
$ms.Dispose()
Write-Output ([Convert]::ToBase64String($bytes))
"""
    r = _run_ps(ps_script, timeout=15)
    b64 = r["stdout"].strip()
    if b64 and len(b64) > 100:
        from PIL import Image
        data = base64.b64decode(b64)
        img = Image.open(io.BytesIO(data))
        if region and len(region) == 4:
            img = img.crop((region[0], region[1], region[0]+region[2], region[1]+region[3]))
        if scale != 1.0:
            img = img.resize((int(img.width * scale), int(img.height * scale)), Image.LANCZOS)
        buf = io.BytesIO()
        img.save(buf, format="PNG", optimize=True)
        data = buf.getvalue()
        return {
            "image_base64": base64.b64encode(data).decode("ascii"),
            "width": img.width, "height": img.height,
            "format": "PNG", "size_bytes": len(data),
        }
    # Fallback: pyautogui / PIL ImageGrab
    try:
        from PIL import ImageGrab, Image
        bbox = tuple(region) if region and len(region) == 4 else None
        img  = ImageGrab.grab(bbox=bbox, all_screens=True)
        if scale != 1.0:
            img = img.resize((int(img.width * scale), int(img.height * scale)), Image.LANCZOS)
        buf = io.BytesIO()
        img.save(buf, format="PNG", optimize=True)
        data = buf.getvalue()
        return {
            "image_base64": base64.b64encode(data).decode("ascii"),
            "width": img.width, "height": img.height,
            "format": "PNG", "size_bytes": len(data),
        }
    except Exception as e:
        return {"error": f"screen grab failed: {e}", "ps_stderr": r.get("stderr", ""), "ps_stdout_len": len(b64)}


@mcp.tool()
def mouse_move(x: int, y: int, duration: float = 0.2) -> dict:
    """
    Move the mouse cursor to screen coordinates (x, y).

    Args:
        x: Screen X coordinate.
        y: Screen Y coordinate.
        duration: Movement duration in seconds (default 0.2).

    Returns: {moved, x, y}
    """
    try:
        pag = _pyautogui()
        pag.moveTo(x, y, duration=duration)
        return {"moved": True, "x": x, "y": y}
    except Exception as e:
        return {"error": str(e)}


@mcp.tool()
def mouse_click(
    x: int,
    y: int,
    button: str = "left",
    clicks: int = 1,
    interval: float = 0.1,
) -> dict:
    """
    Click the mouse at (x, y).

    Args:
        x: Screen X.
        y: Screen Y.
        button: left (default), right, or middle.
        clicks: Number of clicks (2 = double click).
        interval: Seconds between clicks (default 0.1).

    Returns: {clicked, x, y, button, clicks}
    """
    try:
        pag = _pyautogui()
        pag.click(x, y, button=button, clicks=clicks, interval=interval)
        return {"clicked": True, "x": x, "y": y, "button": button, "clicks": clicks}
    except Exception as e:
        return {"error": str(e)}


@mcp.tool()
def keyboard_type(text: str, interval: float = 0.03) -> dict:
    """
    Type a string using simulated keystrokes.

    Args:
        text: Text to type.
        interval: Seconds between keystrokes (default 0.03).

    Returns: {typed, length}
    """
    try:
        pag = _pyautogui()
        pag.typewrite(text, interval=interval)
        return {"typed": True, "length": len(text)}
    except Exception as e:
        # Fallback: SendKeys via PowerShell
        escaped = text.replace("'", "''")
        r = _run_ps(
            f"Add-Type -AssemblyName System.Windows.Forms; "
            f"[System.Windows.Forms.SendKeys]::SendWait('{escaped}')"
        )
        return {"typed": r["exit_code"] == 0, "length": len(text), "stderr": r["stderr"]}


@mcp.tool()
def keyboard_hotkey(keys: str) -> dict:
    """
    Press a hotkey combination.

    Args:
        keys: Keys to press, joined by '+'. Examples:
              'ctrl+c'       -> Ctrl+C (copy)
              'ctrl+shift+esc' -> Task Manager
              'win+d'        -> Show desktop
              'alt+F4'       -> Close window
              'ctrl+alt+del' -> Security screen

    Returns: {sent, keys}
    """
    key_list = [k.strip() for k in keys.split("+") if k.strip()]
    try:
        pag = _pyautogui()
        pag.hotkey(*key_list)
        return {"sent": True, "keys": key_list}
    except Exception as e:
        # Fallback: SendKeys
        key_map = {"ctrl": "^", "alt": "%", "shift": "+"}
        combo = "".join(key_map.get(k.lower(), k) for k in key_list)
        r = _run_ps(
            f"Add-Type -AssemblyName System.Windows.Forms; "
            f"[System.Windows.Forms.SendKeys]::SendWait('{combo}')"
        )
        return {"sent": r["exit_code"] == 0, "keys": key_list}


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
    r = _run_ps(ps, timeout=max(duration + 3, 10))
    return {"shown": r["exit_code"] == 0, "stderr": r["stderr"]}


# ═════════════════════════════════════════════════════════════════════════════
# SECTION 5 — Clipboard
# ═════════════════════════════════════════════════════════════════════════════

@mcp.tool()
def clipboard_get() -> dict:
    """Return current clipboard text."""
    r = _run_ps("Get-Clipboard")
    return {"text": r["stdout"], "exit_code": r["exit_code"]}


@mcp.tool()
def clipboard_set(text: str) -> dict:
    """Write text to the VM clipboard."""
    r = _run_ps(f"Set-Clipboard -Value '{text.replace(chr(39), chr(39)*2)}'")
    return {"success": r["exit_code"] == 0, "stderr": r["stderr"]}


# ═════════════════════════════════════════════════════════════════════════════
# SECTION 6 — Registry
# ═════════════════════════════════════════════════════════════════════════════

@mcp.tool()
def registry_read(path: str, value_name: str = "") -> dict:
    """
    Read a registry value.

    Args:
        path: Registry path, e.g. HKLM\\SOFTWARE\\Nemo
        value_name: Value name (empty = default value).

    Returns: {data, type, path, value_name}
    """
    try:
        root, sub = _split_reg(path)
        with winreg.OpenKey(root, sub, access=winreg.KEY_READ | winreg.KEY_WOW64_64KEY) as k:
            data, rt = winreg.QueryValueEx(k, value_name)
        return {
            "data":       data if not isinstance(data, bytes) else base64.b64encode(data).decode(),
            "type":       _REG_TYPE_NAMES.get(rt, str(rt)),
            "path":       path,
            "value_name": value_name or "(default)",
        }
    except FileNotFoundError:
        return {"error": f"Not found: {path}\\{value_name}"}
    except Exception as e:
        return {"error": str(e)}


@mcp.tool()
def registry_write(path: str, value_name: str, data: Any, reg_type: str = "REG_SZ") -> dict:
    """
    Write a registry value.

    Args:
        path: Registry path.
        value_name: Value name.
        data: Data to write (use base64 string for REG_BINARY).
        reg_type: REG_SZ | REG_DWORD | REG_QWORD | REG_EXPAND_SZ | REG_BINARY | REG_MULTI_SZ.

    Returns: {success, path, value_name}
    """
    try:
        root, sub = _split_reg(path)
        wtype = _REG_TYPE_MAP.get(reg_type.upper(), winreg.REG_SZ)
        if reg_type.upper() == "REG_DWORD":  data = int(data)
        elif reg_type.upper() == "REG_BINARY": data = base64.b64decode(data)
        with winreg.CreateKeyEx(root, sub, access=winreg.KEY_WRITE | winreg.KEY_WOW64_64KEY) as k:
            winreg.SetValueEx(k, value_name, 0, wtype, data)
        return {"success": True, "path": path, "value_name": value_name}
    except Exception as e:
        return {"error": str(e)}


@mcp.tool()
def registry_delete(path: str, value_name: Optional[str] = None) -> dict:
    """
    Delete a registry value or entire key.

    Args:
        path: Registry path.
        value_name: If provided, delete just this value. If None, delete the whole key.

    Returns: {deleted}
    """
    try:
        root, sub = _split_reg(path)
        if value_name is not None:
            with winreg.OpenKey(root, sub, access=winreg.KEY_WRITE | winreg.KEY_WOW64_64KEY) as k:
                winreg.DeleteValue(k, value_name)
        else:
            winreg.DeleteKey(root, sub)
        return {"deleted": True}
    except FileNotFoundError:
        return {"error": f"Not found: {path}"}
    except Exception as e:
        return {"error": str(e)}


@mcp.tool()
def registry_list(path: str) -> dict:
    """
    List subkeys and values in a registry key.

    Args:
        path: Registry path.

    Returns: {subkeys, values, count_subkeys, count_values}
    """
    try:
        root, sub = _split_reg(path)
        with winreg.OpenKey(root, sub, access=winreg.KEY_READ | winreg.KEY_WOW64_64KEY) as k:
            info = winreg.QueryInfoKey(k)
            subkeys = [winreg.EnumKey(k, i) for i in range(info[0])]
            values  = []
            for i in range(info[1]):
                name, data, rt = winreg.EnumValue(k, i)
                values.append({
                    "name": name,
                    "type": _REG_TYPE_NAMES.get(rt, str(rt)),
                    "data": data if not isinstance(data, bytes) else base64.b64encode(data).decode(),
                })
        return {"subkeys": subkeys, "values": values, "count_subkeys": len(subkeys), "count_values": len(values)}
    except FileNotFoundError:
        return {"error": f"Not found: {path}"}
    except Exception as e:
        return {"error": str(e)}


# ═════════════════════════════════════════════════════════════════════════════
# SECTION 7 — Windows Services
# ═════════════════════════════════════════════════════════════════════════════

@mcp.tool()
def service_status(name: str) -> dict:
    """Get the status of a Windows service."""
    r = _run_ps(
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
    r = _run_ps(cmd)
    s = _run_ps(f"(Get-Service -Name '{name}' -ErrorAction SilentlyContinue).Status")
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
    r = _run_ps(cmd, timeout=30)
    try:
        svcs = json.loads(r["stdout"])
        if isinstance(svcs, dict): svcs = [svcs]
        if status_filter:
            svcs = [s for s in svcs if (s.get("Status","") or "").lower() == status_filter.lower()]
        return {"services": svcs, "count": len(svcs)}
    except Exception:
        return {"raw": r["stdout"], "error": r["stderr"]}


# ═════════════════════════════════════════════════════════════════════════════
# SECTION 8 — Networking
# ═════════════════════════════════════════════════════════════════════════════

@mcp.tool()
def net_info() -> dict:
    """
    Return network configuration: adapters, IPs, default gateway, DNS, public IP.
    """
    r = _run_ps(
        "$adapters = Get-NetIPConfiguration -ErrorAction SilentlyContinue; "
        "$out = @(); "
        "foreach ($a in $adapters) { "
        "  $ipv4 = ($a.IPv4Address | ForEach-Object { $_.IPAddress }) -join ','; "
        "  $dns  = ($a.DNSServer   | ForEach-Object { $_.ServerAddresses } | Where-Object { $_ -ne '' }) -join ','; "
        "  $out += [PSCustomObject]@{ "
        "    Interface = $a.InterfaceAlias; "
        "    IPv4      = $ipv4; "
        "    DNS       = $dns "
        "  } "
        "}; "
        "$out | ConvertTo-Json -Compress"
    )
    public_ip = ""
    try:
        public_ip = httpx.get("https://api.ipify.org", timeout=5).text.strip()
    except Exception:
        pass
    gw = _run_ps(
        "(Get-NetRoute -DestinationPrefix '0.0.0.0/0' -ErrorAction SilentlyContinue "
        "| Sort-Object RouteMetric | Select-Object -First 1).NextHop"
    )
    try:
        adapters = json.loads(r["stdout"])
        if isinstance(adapters, dict): adapters = [adapters]
    except Exception:
        adapters = []
    return {
        "adapters":  adapters,
        "public_ip": public_ip,
        "gateway":   gw["stdout"].strip(),
        "hostname":  socket.gethostname(),
    }


@mcp.tool()
def net_ping(host: str, count: int = 4, timeout_ms: int = 1000) -> dict:
    """
    Ping a host and return latency statistics.

    Args:
        host: Hostname or IP address.
        count: Number of ping packets (default 4).
        timeout_ms: Timeout per ping in milliseconds.

    Returns: {host, sent, received, lost_pct, min_ms, max_ms, avg_ms}
    """
    # Use ping.exe directly — Test-Connection parameter set varies across PS versions
    timeout_s = max(1, timeout_ms // 1000)
    result = subprocess.run(
        [r"C:\Windows\System32\ping.exe", "-n", str(count), "-w", str(timeout_ms), host],
        capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=timeout_s * count + 5,
    )
    out = result.stdout
    import re
    times = [int(m) for m in re.findall(r"time[=<](\d+)ms", out, re.IGNORECASE)]
    sent_m  = re.search(r"Sent\s*=\s*(\d+)", out)
    recv_m  = re.search(r"Received\s*=\s*(\d+)", out)
    sent = int(sent_m.group(1)) if sent_m else count
    recv = int(recv_m.group(1)) if recv_m else len(times)
    return {
        "host":     host,
        "sent":     sent,
        "received": recv,
        "lost_pct": round((sent - recv) / sent * 100) if sent else 100,
        "min_ms":   min(times) if times else None,
        "max_ms":   max(times) if times else None,
        "avg_ms":   round(sum(times) / len(times), 1) if times else None,
        "reachable": recv > 0,
    }


@mcp.tool()
def net_port_scan(
    host: str,
    ports: list[int],
    timeout_ms: int = 500,
) -> dict:
    """
    TCP port scan on a host.

    Args:
        host: Target hostname or IP.
        ports: List of port numbers to test (e.g. [22, 80, 443, 3389]).
        timeout_ms: Connection timeout per port in milliseconds.

    Returns: {host, open_ports, closed_ports, results}
    """
    timeout = timeout_ms / 1000
    open_ports, closed_ports = [], []
    results = []
    for port in ports:
        try:
            with socket.create_connection((host, port), timeout=timeout):
                open_ports.append(port)
                results.append({"port": port, "status": "open"})
        except (ConnectionRefusedError, socket.timeout, OSError):
            closed_ports.append(port)
            results.append({"port": port, "status": "closed"})
    return {
        "host":         host,
        "open_ports":   open_ports,
        "closed_ports": closed_ports,
        "results":      results,
        "scanned":      len(ports),
    }


@mcp.tool()
def net_connections(filter_state: str = "") -> dict:
    """
    List active TCP connections and UDP listeners.

    Args:
        filter_state: Optional TCP state filter — ESTABLISHED, LISTEN, TIME_WAIT, etc.

    Returns: {connections, count}
    """
    cmd = "Get-NetTCPConnection"
    if filter_state:
        cmd += f" -State '{filter_state}'"
    cmd += (" | Select-Object LocalAddress,LocalPort,RemoteAddress,RemotePort,"
            "State,OwningProcess | Sort-Object State | ConvertTo-Json -Compress")
    r = _run_ps(cmd, timeout=20)
    try:
        conns = json.loads(r["stdout"])
        if isinstance(conns, dict): conns = [conns]
        return {"connections": conns, "count": len(conns)}
    except Exception:
        return {"raw": r["stdout"], "error": r["stderr"]}


@mcp.tool()
def firewall_rule_list(filter_name: str = "", direction: str = "") -> dict:
    """
    List Windows Firewall rules.

    Args:
        filter_name: Optional display name substring filter.
        direction: Optional direction filter — Inbound or Outbound.

    Returns: {rules, count}
    """
    cmd = "Get-NetFirewallRule"
    if filter_name: cmd += f" -DisplayName '*{filter_name}*'"
    if direction:   cmd += f" -Direction {direction}"
    cmd += (" | Select-Object DisplayName,Direction,Action,Enabled,Profile "
            "| ConvertTo-Json -Compress")
    r = _run_ps(cmd, timeout=30)
    try:
        rules = json.loads(r["stdout"])
        if isinstance(rules, dict): rules = [rules]
        return {"rules": rules, "count": len(rules)}
    except Exception:
        return {"raw": r["stdout"], "error": r["stderr"]}


@mcp.tool()
def firewall_rule_add(
    name: str,
    direction: str = "Inbound",
    action: str = "Allow",
    protocol: str = "TCP",
    local_port: Optional[int] = None,
    remote_address: str = "Any",
    profile: str = "Any",
    description: str = "",
) -> dict:
    """
    Add a Windows Firewall rule.

    Args:
        name: Display name for the rule.
        direction: Inbound (default) or Outbound.
        action: Allow (default) or Block.
        protocol: TCP (default), UDP, or Any.
        local_port: Local port number (None = any port).
        remote_address: Remote IP/CIDR (default Any).
        profile: Any (default), Domain, Private, or Public.
        description: Optional rule description.

    Returns: {success, name, stderr}
    """
    cmd = (
        f"New-NetFirewallRule -DisplayName '{name}' "
        f"-Direction {direction} -Action {action} -Protocol {protocol} "
        f"-RemoteAddress {remote_address} -Profile {profile}"
    )
    if local_port:
        cmd += f" -LocalPort {local_port}"
    if description:
        cmd += f" -Description '{description.replace(chr(39), chr(39)*2)}'"
    r = _run_ps(cmd)
    return {"success": r["exit_code"] == 0, "name": name, "stderr": r["stderr"]}


@mcp.tool()
def firewall_rule_remove(name: str) -> dict:
    """Remove a Windows Firewall rule by display name."""
    r = _run_ps(f"Remove-NetFirewallRule -DisplayName '{name}' -ErrorAction SilentlyContinue")
    return {"success": r["exit_code"] == 0, "name": name, "stderr": r["stderr"]}


# ═════════════════════════════════════════════════════════════════════════════
# SECTION 9 — System
# ═════════════════════════════════════════════════════════════════════════════

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
        r_cpu  = _run_ps("(Get-CimInstance Win32_Processor | Measure-Object -Property NumberOfCores -Sum).Sum")
        r_ram  = _run_ps("(Get-CimInstance Win32_ComputerSystem).TotalPhysicalMemory")
        r_disk = _run_ps("Get-PSDrive C | Select-Object @{N='U';E={[math]::Round($_.Used/1GB,1)}},@{N='F';E={[math]::Round($_.Free/1GB,1)}} | ConvertTo-Json -Compress")
        r_up   = _run_ps("(Get-Date) - (gcim Win32_OperatingSystem).LastBootUpTime | Select-Object -ExpandProperty TotalHours")
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
        "mcp_version": SERVER_VERSION,
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
    r = _run_ps(cmd, timeout=30)
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
    r = _run_ps(cmd, timeout=20)
    try:
        tasks = json.loads(r["stdout"])
        if isinstance(tasks, dict): tasks = [tasks]
        return {"tasks": tasks, "count": len(tasks)}
    except Exception:
        return {"raw": r["stdout"], "error": r["stderr"]}


@mcp.tool()
def scheduled_task_run(name: str) -> dict:
    """Immediately run a scheduled task by name."""
    r = _run_ps(f"Start-ScheduledTask -TaskName '{name}' -ErrorAction Stop")
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
    r = _run_ps(f"[System.Environment]::GetEnvironmentVariable('{name}', '{scope}')")
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
        r = _run_ps(f"[System.Environment]::SetEnvironmentVariable('{name}', $null, '{scope}')")
    else:
        r = _run_ps(f"[System.Environment]::SetEnvironmentVariable('{name}', '{value.replace(chr(39), chr(39)*2)}', '{scope}')")
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
    r = _run_ps(ps, timeout=30)
    try:
        drivers = json.loads(r["stdout"])
        if isinstance(drivers, dict): drivers = [drivers]
        return {"drivers": drivers, "count": len(drivers)}
    except Exception:
        return {"raw": r["stdout"][:3000], "error": r["stderr"]}


# ═════════════════════════════════════════════════════════════════════════════
# SECTION 10 — Software & packages
# ═════════════════════════════════════════════════════════════════════════════

@mcp.tool()
def download_file(url: str, destination: str, timeout_seconds: int = 300) -> dict:
    """
    Download a file from the internet to the VM.

    Args:
        url: URL to download.
        destination: Local destination path.
        timeout_seconds: Download timeout (default 300 s).

    Returns: {success, path, size_bytes}
    """
    dst = Path(destination)
    dst.parent.mkdir(parents=True, exist_ok=True)
    r = _run_ps(
        f"Invoke-WebRequest -Uri '{url}' -OutFile '{destination}' "
        f"-UseBasicParsing -TimeoutSec {timeout_seconds}",
        timeout=timeout_seconds + 10,
    )
    if r["exit_code"] != 0:
        return {"success": False, "stderr": r["stderr"]}
    size = dst.stat().st_size if dst.exists() else 0
    return {"success": True, "path": str(dst.resolve()), "size_bytes": size}


@mcp.tool()
def install_msi(
    source: str,
    silent_args: str = "/quiet /norestart",
    is_url: bool = False,
    timeout_seconds: int = 300,
) -> dict:
    """
    Install an MSI, EXE, or other installer silently.

    Args:
        source: Local path or URL (if is_url=True).
        silent_args: Arguments for the installer (default /quiet /norestart).
        is_url: Download source URL to temp file first.
        timeout_seconds: Install timeout.

    Returns: {success, installer_exit_code, stderr}
    """
    local = source
    tmp   = None
    if is_url:
        ext = Path(source.split("?")[0]).suffix or ".exe"
        tmp = Path(tempfile.mktemp(suffix=ext))
        dl  = download_file(source, str(tmp), timeout_seconds=120)
        if not dl.get("success"):
            return {"success": False, "error": "Download failed", **dl}
        local = str(tmp)

    p = Path(local)
    if not p.exists():
        return {"success": False, "error": f"Installer not found: {local}"}

    if p.suffix.lower() == ".msi":
        cmd = (f'Start-Process msiexec.exe -ArgumentList "/i \\"{local}\\" {silent_args}" '
               f"-Wait -PassThru | Select-Object -ExpandProperty ExitCode")
    else:
        cmd = (f'Start-Process \\"{local}\\" -ArgumentList "{silent_args}" '
               f"-Wait -PassThru | Select-Object -ExpandProperty ExitCode")

    r = _run_ps(cmd, timeout=timeout_seconds)
    if tmp: tmp.unlink(missing_ok=True)
    return {
        "success":             r["exit_code"] == 0,
        "installer_exit_code": r["stdout"].strip(),
        "stderr":              r["stderr"].strip(),
    }


# ═════════════════════════════════════════════════════════════════════════════
# SECTION 11 — Server self-management  ★ NEW
# ═════════════════════════════════════════════════════════════════════════════

@mcp.tool()
def server_version() -> dict:
    """
    Return current server version, install path, and uptime.

    Returns: {version, file, install_dir, pid, uptime_seconds, python}
    """
    return {
        "version":       SERVER_VERSION,
        "file":          str(SERVER_FILE),
        "install_dir":   str(INSTALL_DIR),
        "pid":           os.getpid(),
        "python":        sys.version,
        "update_url":    UPDATE_URL,
        "log_file":      str(LOG_FILE),
    }


@mcp.tool()
def server_update(restart: bool = False, url: str = "") -> dict:
    """
    Fetch the latest server.py from GitHub and replace the running copy.

    Downloads src/server.py from UPDATE_URL (or the provided url),
    writes it next to the current server.py, and optionally triggers
    server_restart() to apply the update immediately.

    Args:
        restart: If True, restart the server process after updating (default False).
        url: Override the default update URL (leave blank to use UPDATE_URL env var).

    Returns: {updated, version_before, bytes_downloaded, path, restarting}
    """
    target_url = url or UPDATE_URL
    log.info("server_update: fetching %s", target_url)

    try:
        resp = httpx.get(target_url, timeout=30, follow_redirects=True)
        resp.raise_for_status()
    except Exception as e:
        return {"updated": False, "error": str(e)}

    new_code = resp.text
    new_bytes = len(resp.content)

    # Basic sanity check: must contain the FastMCP marker
    if "FastMCP" not in new_code and "fastmcp" not in new_code:
        return {"updated": False, "error": "Downloaded file does not look like a valid server.py"}

    # Write to a temp file first, then atomically replace
    tmp = SERVER_FILE.with_suffix(".py.tmp")
    tmp.write_text(new_code, encoding="utf-8")
    shutil.move(str(tmp), SERVER_FILE)

    log.info("server_update: wrote %d bytes to %s", new_bytes, SERVER_FILE)

    result = {
        "updated":          True,
        "version_before":   SERVER_VERSION,
        "bytes_downloaded": new_bytes,
        "path":             str(SERVER_FILE),
        "update_url":       target_url,
        "restarting":       restart,
    }

    if restart:
        # Schedule restart after a short delay so the HTTP response can be sent
        def _do_restart():
            time.sleep(2)
            log.info("server_update: restarting process (pid %d)…", os.getpid())
            os.execv(sys.executable, [sys.executable] + sys.argv)
        threading.Thread(target=_do_restart, daemon=True).start()

    return result


@mcp.tool()
def server_restart() -> dict:
    """
    Restart the vm-mcp server process (re-exec with same argv).
    Use this after server_update() to apply the new code.

    Returns: {restarting, pid}  — the response is sent before the restart.
    """
    log.info("server_restart: re-exec requested (pid %d)", os.getpid())
    def _restart():
        time.sleep(1.5)
        os.execv(sys.executable, [sys.executable] + sys.argv)
    threading.Thread(target=_restart, daemon=True).start()
    return {"restarting": True, "pid": os.getpid(), "new_argv": sys.argv}


@mcp.tool()
def server_log_tail(lines: int = 50) -> dict:
    """
    Return the last N lines of the server log file.

    Args:
        lines: Number of tail lines (default 50, max 500).

    Returns: {log_file, content, lines_returned}
    """
    lines = min(lines, 500)
    if not LOG_FILE.exists():
        return {"log_file": str(LOG_FILE), "content": "", "lines_returned": 0}
    with open(LOG_FILE, encoding="utf-8", errors="replace") as f:
        all_lines = f.readlines()
    tail = all_lines[-lines:]
    return {
        "log_file":      str(LOG_FILE),
        "content":       "".join(tail),
        "lines_returned": len(tail),
        "total_lines":   len(all_lines),
    }


# ═════════════════════════════════════════════════════════════════════════════
# SECTION 12 — Power management
# ═════════════════════════════════════════════════════════════════════════════

@mcp.tool()
def reboot(delay_seconds: int = 10, message: str = "VM MCP reboot") -> dict:
    """Schedule a VM reboot."""
    delay_seconds = max(0, min(delay_seconds, 3600))
    r = _run_ps(f'shutdown.exe /r /t {delay_seconds} /c "{message}"')
    return {"scheduled": r["exit_code"] == 0, "delay_seconds": delay_seconds, "stderr": r["stderr"].strip()}


@mcp.tool()
def reboot_cancel() -> dict:
    """Cancel a pending shutdown or reboot."""
    r = _run_ps("shutdown.exe /a")
    return {"cancelled": r["exit_code"] == 0, "stderr": r["stderr"].strip()}


@mcp.tool()
def shutdown(delay_seconds: int = 10, message: str = "VM MCP shutdown") -> dict:
    """Schedule a VM shutdown (power off)."""
    delay_seconds = max(0, min(delay_seconds, 3600))
    r = _run_ps(f'shutdown.exe /s /t {delay_seconds} /c "{message}"')
    return {"scheduled": r["exit_code"] == 0, "delay_seconds": delay_seconds, "stderr": r["stderr"].strip()}


# ═════════════════════════════════════════════════════════════════════════════
# SECTION 13 — AI Cursor & Input  (purple overlay + SendInput)
# ═════════════════════════════════════════════════════════════════════════════
#
# Architecture
# ────────────
#  • ai_cursor.py  — a tkinter overlay that draws a purple arrow on the
#    screen and listens on localhost:8788 for move/show/hide commands.
#    It must run INSIDE the interactive user session (Session 1) to be
#    visible.  We launch it via _launch_in_session() every time the active
#    console session changes.
#
#  • Input injection uses SendInput (user32.dll) via ctypes executed in
#    Session 1 through a tiny ps1 helper launched with _launch_in_session.
#    SendInput bypasses the Session-0 barrier and works reliably.
#
#  • The MCP server tracks the last known active session and the PID of the
#    overlay process.  ai_cursor_start() is idempotent — it checks if the
#    overlay is already alive via /ping before re-launching.
# ─────────────────────────────────────────────────────────────────────────────

import urllib.request as _urllib_req
import urllib.error  as _urllib_err

AI_CURSOR_PORT   = 8788
AI_CURSOR_SCRIPT = str(INSTALL_DIR / "ai_cursor.py")

# In-memory state (not persisted across restarts)
_ai_cursor_pid: Optional[int] = None
_ai_cursor_x:   int = 100
_ai_cursor_y:   int = 100


def _cursor_ping() -> bool:
    """Return True if the overlay HTTP server is responding."""
    try:
        req = _urllib_req.Request(
            f"http://127.0.0.1:{AI_CURSOR_PORT}/ping",
            data=b"{}",
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with _urllib_req.urlopen(req, timeout=2) as r:
            return r.status == 200
    except Exception:
        return False


def _cursor_post(endpoint: str, payload: dict) -> dict:
    """POST to the overlay HTTP server.  Returns {} on failure."""
    try:
        body = json.dumps(payload).encode()
        req  = _urllib_req.Request(
            f"http://127.0.0.1:{AI_CURSOR_PORT}{endpoint}",
            data=body,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with _urllib_req.urlopen(req, timeout=3) as r:
            return json.loads(r.read())
    except Exception as e:
        return {"error": str(e)}


def _ensure_cursor_running() -> dict:
    """
    Make sure ai_cursor.py is running in the current active session.
    Launches it if it isn't, or if the session has changed.
    Returns {running, pid, session_id}.
    """
    global _ai_cursor_pid
    session_id = _get_interactive_session_id()

    if _cursor_ping():
        return {"running": True, "pid": _ai_cursor_pid, "session_id": session_id}

    # Find Python in the user's session
    python_paths = [
        r"C:\Users\User0\AppData\Local\Python\pythoncore-3.15-64\python.exe",
        r"C:\Users\User0\AppData\Local\Programs\Python\Python312\python.exe",
        r"C:\Users\User0\AppData\Local\Programs\Python\Python311\python.exe",
        r"C:\Python312\python.exe",
        r"C:\Python311\python.exe",
        sys.executable,
    ]
    python = next((p for p in python_paths if Path(p).exists()), sys.executable)

    result = _launch_in_session(python, f'"{AI_CURSOR_SCRIPT}"', session_id=session_id)
    if not result["started"]:
        return {"running": False, "error": result.get("error"), "session_id": session_id}

    _ai_cursor_pid = result["pid"]
    # Give it a moment to start
    time.sleep(2)
    alive = _cursor_ping()
    return {"running": alive, "pid": _ai_cursor_pid, "session_id": session_id,
            "note": "Overlay launched in active session"}


def _run_ps1_in_session(ps_content: str, wait_seconds: float = 1.5) -> dict:
    """
    Write ps_content to a temp .ps1 file, launch it via _launch_in_session
    (so it runs in Session 1 with full desktop access), wait, then delete.
    This is the ONLY reliable way to run SendInput/SetCursorPos from Session 0.
    """
    script = Path(tempfile.mktemp(suffix=".ps1", dir=r"C:\VmMcp"))
    script.write_text(ps_content, encoding="utf-8-sig")
    result = _launch_in_session(
        PWSH,
        f'-ExecutionPolicy Bypass -NonInteractive -WindowStyle Hidden -File "{script}"',
    )
    time.sleep(wait_seconds)
    script.unlink(missing_ok=True)
    return result


def _sendinput_ps(x: int, y: int, action: str = "move",
                  button: str = "left", scroll_delta: int = 0) -> dict:
    """
    Inject mouse input via SendInput + SetCursorPos in the user session.
    Writes a temp PS1 and launches it via _launch_in_session (proven working).
    """
    # Screen resolution for absolute coord normalisation
    ps_res = _run_ps(
        "Add-Type -AssemblyName System.Windows.Forms; "
        "$s=[System.Windows.Forms.Screen]::PrimaryScreen.Bounds; "
        "Write-Output \"$($s.Width) $($s.Height)\""
    )
    try:
        w, h = map(int, ps_res["stdout"].strip().split())
    except Exception:
        w, h = 1024, 768

    # SendInput absolute coords are 0-65535
    abs_x = int(x * 65535 / w)
    abs_y = int(y * 65535 / h)

    MOVE     = 0x0001
    LDOWN    = 0x0002
    LUP      = 0x0004
    RDOWN    = 0x0008
    RUP      = 0x0010
    MDOWN    = 0x0020
    MUP      = 0x0040
    WHEEL    = 0x0800
    ABS      = 0x8000

    btn_down = {"left": LDOWN, "right": RDOWN, "middle": MDOWN}
    btn_up   = {"left": LUP,   "right": RUP,   "middle": MUP}

    SENDINPUT_CS = r"""
using System;
using System.Runtime.InteropServices;
public class WinInput {
    [StructLayout(LayoutKind.Sequential)]
    public struct MOUSEINPUT { public int dx, dy, mouseData, dwFlags, time; public IntPtr dwExtraInfo; }
    [StructLayout(LayoutKind.Sequential)]
    public struct INPUT { public int type; public MOUSEINPUT mi; }
    [DllImport("user32.dll")] public static extern uint SendInput(uint n, INPUT[] inputs, int cb);
    [DllImport("user32.dll")] public static extern bool SetCursorPos(int x, int y);
}
"""

    if action == "move":
        ps = f"""
Add-Type @"
{SENDINPUT_CS}
"@
[WinInput]::SetCursorPos({x}, {y}) | Out-Null
$i = New-Object WinInput+INPUT; $i.type=0
$i.mi.dx={abs_x}; $i.mi.dy={abs_y}; $i.mi.dwFlags={MOVE|ABS}; $i.mi.mouseData=0
[WinInput]::SendInput(1, @($i), [System.Runtime.InteropServices.Marshal]::SizeOf($i)) | Out-Null
"""
    elif action == "click":
        df = MOVE | ABS | btn_down.get(button, LDOWN)
        uf = MOVE | ABS | btn_up.get(button, LUP)
        ps = f"""
Add-Type @"
{SENDINPUT_CS}
"@
function Send-MI([int]$dx,[int]$dy,[int]$f,[int]$d) {{
    $i = New-Object WinInput+INPUT; $i.type=0
    $i.mi.dx=$dx; $i.mi.dy=$dy; $i.mi.dwFlags=$f; $i.mi.mouseData=$d
    [WinInput]::SendInput(1, @($i), [System.Runtime.InteropServices.Marshal]::SizeOf($i)) | Out-Null
}}
[WinInput]::SetCursorPos({x}, {y}) | Out-Null
Send-MI {abs_x} {abs_y} {MOVE|ABS} 0
Start-Sleep -Milliseconds 50
Send-MI {abs_x} {abs_y} {df} 0
Start-Sleep -Milliseconds 80
Send-MI {abs_x} {abs_y} {uf} 0
"""
    elif action == "scroll":
        ps = f"""
Add-Type @"
{SENDINPUT_CS}
"@
[WinInput]::SetCursorPos({x}, {y}) | Out-Null
$i = New-Object WinInput+INPUT; $i.type=0
$i.mi.dx=0; $i.mi.dy=0; $i.mi.dwFlags={WHEEL}; $i.mi.mouseData={scroll_delta}
[WinInput]::SendInput(1, @($i), [System.Runtime.InteropServices.Marshal]::SizeOf($i)) | Out-Null
"""
    else:
        ps = f"""
Add-Type @"
{SENDINPUT_CS}
"@
[WinInput]::SetCursorPos({x}, {y}) | Out-Null
"""

    _run_ps1_in_session(ps, wait_seconds=0.5 if action == "move" else 1.0)
    return {"ok": True, "x": x, "y": y, "action": action}


def _sendinput_key_ps(keys: list[str], text: str = "") -> dict:
    """
    Inject keyboard input via SendInput in the user session.
    Writes a temp PS1 and launches via _launch_in_session (proven working).
    keys: list of VK names like ["ctrl","c"] or ["enter"]
    text: Unicode string to type character by character
    """
    VK = {
        "enter": 0x0D, "return": 0x0D, "tab": 0x09, "space": 0x20,
        "backspace": 0x08, "delete": 0x2E, "escape": 0x1B, "esc": 0x1B,
        "up": 0x26, "down": 0x28, "left": 0x25, "right": 0x27,
        "home": 0x24, "end": 0x23, "pageup": 0x21, "pagedown": 0x22,
        "f1":0x70,"f2":0x71,"f3":0x72,"f4":0x73,"f5":0x74,"f6":0x75,
        "f7":0x76,"f8":0x77,"f9":0x78,"f10":0x79,"f11":0x7A,"f12":0x7B,
        "ctrl": 0x11, "control": 0x11, "shift": 0x10, "alt": 0x12,
        "win": 0x5B, "windows": 0x5B,
        "a":0x41,"b":0x42,"c":0x43,"d":0x44,"e":0x45,"f":0x46,"g":0x47,
        "h":0x48,"i":0x49,"j":0x4A,"k":0x4B,"l":0x4C,"m":0x4D,"n":0x4E,
        "o":0x4F,"p":0x50,"q":0x51,"r":0x52,"s":0x53,"t":0x54,"u":0x55,
        "v":0x56,"w":0x57,"x":0x58,"y":0x59,"z":0x5A,
        "0":0x30,"1":0x31,"2":0x32,"3":0x33,"4":0x34,
        "5":0x35,"6":0x36,"7":0x37,"8":0x38,"9":0x39,
    }

    KI_CS = r"""
using System;
using System.Runtime.InteropServices;
public class WinKey {
    [StructLayout(LayoutKind.Sequential)]
    public struct KEYBDINPUT { public ushort wVk, wScan; public uint dwFlags, time; public IntPtr dwExtraInfo; }
    [StructLayout(LayoutKind.Sequential)]
    public struct INPUT { public int type; public KEYBDINPUT ki; }
    [DllImport("user32.dll")] public static extern uint SendInput(uint n, INPUT[] inputs, int cb);
}
"""

    if text:
        lines = [f'Add-Type @"\n{KI_CS}\n"@']
        lines.append("$sz = [System.Runtime.InteropServices.Marshal]::SizeOf((New-Object WinKey+INPUT))")
        lines.append("""function Send-Char([int]$scan) {
    $dn = New-Object WinKey+INPUT; $dn.type=1; $dn.ki.wVk=0; $dn.ki.wScan=$scan; $dn.ki.dwFlags=4
    $up = New-Object WinKey+INPUT; $up.type=1; $up.ki.wVk=0; $up.ki.wScan=$scan; $up.ki.dwFlags=6
    [WinKey]::SendInput(1,@($dn),$sz) | Out-Null
    Start-Sleep -Milliseconds 20
    [WinKey]::SendInput(1,@($up),$sz) | Out-Null
    Start-Sleep -Milliseconds 20
}""")
        for ch in text:
            lines.append(f"Send-Char {ord(ch)}")
        ps = "\n".join(lines)
        wait = max(2.0, len(text) * 0.05)
    else:
        vk_list = [VK.get(k.lower(), ord(k.upper()) if len(k)==1 else 0) for k in keys]
        vk_list = [v for v in vk_list if v]
        down_lines = "\n".join(
            f"$d{i}=New-Object WinKey+INPUT;$d{i}.type=1;$d{i}.ki.wVk={vk};$d{i}.ki.dwFlags=0;[WinKey]::SendInput(1,@($d{i}),$sz)|Out-Null;Start-Sleep -Milliseconds 50"
            for i, vk in enumerate(vk_list)
        )
        up_lines = "\n".join(
            f"$u{i}=New-Object WinKey+INPUT;$u{i}.type=1;$u{i}.ki.wVk={vk};$u{i}.ki.dwFlags=2;[WinKey]::SendInput(1,@($u{i}),$sz)|Out-Null;Start-Sleep -Milliseconds 50"
            for i, vk in reversed(list(enumerate(vk_list)))
        )
        ps = f"""Add-Type @"
{KI_CS}
"@
$sz = [System.Runtime.InteropServices.Marshal]::SizeOf((New-Object WinKey+INPUT))
{down_lines}
{up_lines}
"""
        wait = 1.0

    _run_ps1_in_session(ps, wait_seconds=wait)
    return {"ok": True, "keys": keys, "text_length": len(text)}


def _ai_screenshot_with_cursor() -> dict:
    """Take a screenshot via schtasks (Session 1 capture) and draw the purple cursor on it."""
    import subprocess as _sp

    # Trigger the screenshot schtasks (must be pre-configured — see install.ps1)
    _run_ps("schtasks /run /tn ScreenCap3 2>$null; Start-Sleep 2; schtasks /run /tn ToJpg2 2>$null; Start-Sleep 2")

    jpg = Path(r"C:\Users\User0\Desktop\screen.jpg")
    if not jpg.exists():
        # Fallback: try pyautogui / PIL from Session 0 (may be blank but worth trying)
        return screenshot(scale=0.75)

    try:
        from PIL import Image, ImageDraw
        img  = Image.open(jpg).convert("RGB")
        draw = ImageDraw.Draw(img)

        # Scale cursor position to screenshot size
        ps_res = _run_ps(
            "Add-Type -AssemblyName System.Windows.Forms; "
            "$s=[System.Windows.Forms.Screen]::PrimaryScreen.Bounds; "
            "Write-Output \"$($s.Width) $($s.Height)\""
        )
        try:
            sw, sh = map(int, ps_res["stdout"].strip().split())
        except Exception:
            sw, sh = 1920, 1080

        cx = int(_ai_cursor_x * img.width  / sw)
        cy = int(_ai_cursor_y * img.height / sh)

        # Draw purple arrow at cursor position
        FILL  = (155, 48, 255)
        OUTL  = (61,  0, 112)
        pts = [
            (cx,    cy),
            (cx,    cy+20),
            (cx+5,  cy+15),
            (cx+8,  cy+22),
            (cx+11, cy+21),
            (cx+8,  cy+14),
            (cx+14, cy+14),
        ]
        draw.polygon(pts, fill=FILL, outline=OUTL)
        draw.ellipse([cx, cy, cx+4, cy+4], fill=FILL, outline=OUTL)

        buf = io.BytesIO()
        img.save(buf, format="JPEG", quality=85)
        data = buf.getvalue()
        return {
            "image_base64": base64.b64encode(data).decode("ascii"),
            "width": img.width, "height": img.height,
            "format": "JPEG", "size_bytes": len(data),
            "cursor_x": _ai_cursor_x, "cursor_y": _ai_cursor_y,
        }
    except Exception as e:
        return {"error": f"screenshot failed: {e}"}


# ── MCP Tools ─────────────────────────────────────────────────────────────────

@mcp.tool()
def ai_cursor_start() -> dict:
    """
    Launch the purple AI cursor overlay in the currently active user session.

    The overlay is a transparent always-on-top window that draws a purple
    arrow cursor.  It automatically appears on whatever session is currently
    visible on the physical display.  Safe to call multiple times — it will
    not launch a second instance if one is already running.

    Returns: {running, pid, session_id}
    """
    return _ensure_cursor_running()


@mcp.tool()
def ai_cursor_stop() -> dict:
    """
    Kill the AI cursor overlay process.

    Returns: {stopped}
    """
    global _ai_cursor_pid
    if _ai_cursor_pid:
        result = process_kill(pid=_ai_cursor_pid)
        _ai_cursor_pid = None
        return {"stopped": True, **result}
    # Also try by name
    result = process_kill(name="python", force=False)
    return {"stopped": True, "note": "killed by name (no tracked PID)"}


@mcp.tool()
def ai_cursor_status() -> dict:
    """
    Check whether the AI cursor overlay is running and get its current position.

    Returns: {running, x, y, visible, pid, session_id}
    """
    alive = _cursor_ping()
    pos   = _cursor_post("/ping", {}) if alive else {}
    return {
        "running":    alive,
        "x":          pos.get("x", _ai_cursor_x),
        "y":          pos.get("y", _ai_cursor_y),
        "visible":    pos.get("visible", False),
        "pid":        _ai_cursor_pid,
        "session_id": _get_interactive_session_id(),
    }


@mcp.tool()
def ai_mouse_move(x: int, y: int) -> dict:
    """
    Move the AI cursor to screen coordinates (x, y).

    This does TWO things:
      1. Moves the purple overlay cursor to (x, y) so it's visible on screen.
      2. Injects a real mouse-move via SendInput so the OS cursor also moves
         to that position inside Session 1 (the interactive desktop).

    Args:
        x: Screen X coordinate (pixels from left).
        y: Screen Y coordinate (pixels from top).

    Returns: {moved, x, y}
    """
    global _ai_cursor_x, _ai_cursor_y
    _ai_cursor_x = x
    _ai_cursor_y = y

    # Move overlay cursor
    _ensure_cursor_running()
    _cursor_post("/move", {"x": x, "y": y})

    # Inject real mouse move via SendInput in Session 1
    _sendinput_ps(x, y, action="move")

    return {"moved": True, "x": x, "y": y}


@mcp.tool()
def ai_mouse_click(
    x: int,
    y: int,
    button: str = "left",
    clicks: int = 1,
) -> dict:
    """
    Move the AI cursor to (x, y) and click.

    Uses SendInput to inject real mouse events into the interactive desktop
    session — works even from the SYSTEM service (Session 0 → Session 1).
    The purple cursor overlay also moves to show where the click happened.

    Args:
        x:       Screen X coordinate.
        y:       Screen Y coordinate.
        button:  left (default), right, or middle.
        clicks:  Number of clicks — 1 (default) or 2 (double-click).

    Returns: {clicked, x, y, button, clicks}
    """
    global _ai_cursor_x, _ai_cursor_y
    _ai_cursor_x = x
    _ai_cursor_y = y

    # Move overlay
    _ensure_cursor_running()
    _cursor_post("/move", {"x": x, "y": y})

    # Inject click(s) via SendInput
    for _ in range(clicks):
        _sendinput_ps(x, y, action="click", button=button)
        if clicks > 1:
            time.sleep(0.1)

    return {"clicked": True, "x": x, "y": y, "button": button, "clicks": clicks}


@mcp.tool()
def ai_mouse_scroll(x: int, y: int, direction: str = "down", amount: int = 3) -> dict:
    """
    Scroll the mouse wheel at position (x, y).

    Args:
        x:         Screen X.
        y:         Screen Y.
        direction: down (default) or up.
        amount:    Number of scroll notches (default 3).

    Returns: {scrolled, direction, amount}
    """
    delta = -120 * amount if direction == "down" else 120 * amount
    _sendinput_ps(x, y, action="scroll", scroll_delta=delta)
    return {"scrolled": True, "direction": direction, "amount": amount}


@mcp.tool()
def ai_key_press(keys: str) -> dict:
    """
    Press a key or key combination via SendInput in the interactive session.

    Args:
        keys: Key combo joined by '+'.  Examples:
              'enter'          → press Enter
              'ctrl+c'         → Ctrl+C (copy)
              'ctrl+shift+esc' → Task Manager
              'win+d'          → Show desktop
              'alt+F4'         → Close window
              'ctrl+l'         → focus address bar in browser
              'f5'             → refresh

    Returns: {sent, keys}
    """
    key_list = [k.strip() for k in keys.split("+") if k.strip()]
    result = _sendinput_key_ps(key_list)
    return {"sent": result["ok"], "keys": key_list}


@mcp.tool()
def ai_type(text: str) -> dict:
    """
    Type a string into the focused window via SendInput (Unicode-aware).

    Unlike keyboard_type (which uses pyautogui/SendKeys and fails in Session 1),
    this injects real KEYEVENTF_UNICODE events directly into the interactive
    desktop session.  Works for any character including non-ASCII.

    Args:
        text: String to type.

    Returns: {typed, length}
    """
    result = _sendinput_key_ps([], text=text)
    return {"typed": result["ok"], "length": len(text)}


@mcp.tool()
def ai_screenshot() -> dict:
    """
    Capture the VM desktop as seen by the user, with the purple AI cursor
    drawn at its current position.

    Uses the schtasks screenshot method (captures Session 1 — the real
    desktop) and overlays the purple cursor arrow before returning the image.

    Returns: {image_base64, width, height, format, cursor_x, cursor_y}
    """
    return _ai_screenshot_with_cursor()


# ═════════════════════════════════════════════════════════════════════════════
# Entry point
# ═════════════════════════════════════════════════════════════════════════════

def main():
    import uvicorn
    from starlette.applications import Starlette
    from starlette.middleware import Middleware
    from starlette.routing import Mount

    app  = mcp.http_app(transport="sse")
    wrapped = Starlette(
        routes=[Mount("/", app=app)],
        middleware=[Middleware(AuthMiddleware)],
    )

    # Count registered tools — count @mcp.tool() decorators at module level
    import inspect, sys
    tool_count = sum(
        1 for name, obj in inspect.getmembers(sys.modules[__name__])
        if callable(obj) and getattr(obj, "_mcp_tool", None) is not None
    )
    if tool_count == 0:
        # fallback: grep own source for @mcp.tool()
        try:
            tool_count = open(__file__).read().count("@mcp.tool()")
        except Exception:
            tool_count = "?"

    print(f"""
╔══════════════════════════════════════════════════════════╗
║          Nemo VM MCP Server  v{SERVER_VERSION:<27}║
╠══════════════════════════════════════════════════════════╣
║  Listening : {HOST}:{PORT:<40}║
║  SSE URL   : http://<your-ip>:{PORT}/sse               ║
║  Tools     : {tool_count:<43}║
║  Auth      : X-Api-Key header required                  ║
║  Log       : {str(LOG_FILE):<43}║
╚══════════════════════════════════════════════════════════╝

Kiro CLI mcp.json snippet:
  "vm-mcp": {{
    "url": "http://<VM-PUBLIC-IP>:{PORT}/sse",
    "headers": {{"X-Api-Key": "<your-key>"}}
  }}

Self-update:  call server_update(restart=True) to fetch latest from:
  {UPDATE_URL}
""")

    uvicorn.run(wrapped, host=HOST, port=PORT, log_level="info")


if __name__ == "__main__":
    main()
