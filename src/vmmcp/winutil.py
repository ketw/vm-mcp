"""winutil.py — low-level Windows helpers shared by tool modules.

  run_ps()                  run PowerShell (optionally as SYSTEM via scheduled task)
  winget_path()             locate the real winget.exe (the Store alias fails under SYSTEM)
  get_interactive_session_id()   session attached to the physical console
  current_session_id()      session this very process lives in
  launch_in_session()       CreateProcessAsUser into the user's desktop (Session 0 -> Session N)
  run_in_user_session()     run a PowerShell snippet on the interactive desktop
"""

from __future__ import annotations

import ctypes
import ctypes.wintypes as wt
import glob
import os
import subprocess
import tempfile
import time
from datetime import datetime
from pathlib import Path
from typing import Optional

from .config import PWSH, SCHTASKS, SHELL_TIMEOUT

# ── PowerShell ───────────────────────────────────────────────────────────────


def run_ps(command: str, timeout: int = SHELL_TIMEOUT, elevated: bool = False) -> dict:
    """Execute a PowerShell command; return {stdout, stderr, exit_code}."""
    if elevated:
        task = f"VmMcp_{datetime.now().strftime('%H%M%S%f')}"
        script = Path(tempfile.mktemp(suffix=".ps1"))
        out = Path(tempfile.mktemp(suffix=".txt"))
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
        script.unlink(missing_ok=True)
        out.unlink(missing_ok=True)
        return {"stdout": stdout.rstrip("\n"), "stderr": "", "exit_code": 0}

    result = subprocess.run(
        [PWSH, "-ExecutionPolicy", "Bypass", "-NonInteractive", "-Command", command],
        capture_output=True, text=True, timeout=timeout, encoding="utf-8", errors="replace",
    )
    return {
        "stdout": result.stdout.rstrip("\n"),
        "stderr": result.stderr.rstrip("\n"),
        "exit_code": result.returncode,
    }


# ── winget ───────────────────────────────────────────────────────────────────
_winget_cache = ""


def winget_path() -> str:
    """Find the real winget.exe (not the WindowsApps Store alias). '' if not found."""
    global _winget_cache
    if _winget_cache and Path(_winget_cache).exists():
        return _winget_cache

    r = run_ps(
        "(Get-AppxPackage Microsoft.DesktopAppInstaller -ErrorAction SilentlyContinue).InstallLocation",
        timeout=10,
    )
    install_loc = r["stdout"].strip()
    candidates = [str(Path(install_loc) / "winget.exe")] if install_loc else []
    candidates += sorted(glob.glob(r"C:\Program Files\WindowsApps\Microsoft.DesktopAppInstaller_*\winget.exe"), reverse=True)
    candidates.append(os.path.expandvars(r"%LOCALAPPDATA%\Microsoft\WindowsApps\winget.exe"))
    for c in candidates:
        if Path(c).exists():
            _winget_cache = c
            return c
    return ""


# ── Sessions ─────────────────────────────────────────────────────────────────


def get_interactive_session_id() -> int:
    """Session id attached to the physical console (0xFFFFFFFF = nobody logged in)."""
    k32 = ctypes.WinDLL("kernel32.dll", use_last_error=True)
    k32.WTSGetActiveConsoleSessionId.restype = ctypes.c_uint32
    return k32.WTSGetActiveConsoleSessionId()


def current_session_id() -> int:
    """Session id of this process (0 when running as a Windows service)."""
    k32 = ctypes.WinDLL("kernel32.dll", use_last_error=True)
    sid = wt.DWORD(0)
    k32.ProcessIdToSessionId(os.getpid(), ctypes.byref(sid))
    return sid.value


class _STARTUPINFOW(ctypes.Structure):
    _fields_ = [
        ("cb", wt.DWORD), ("lpReserved", wt.LPWSTR), ("lpDesktop", wt.LPWSTR), ("lpTitle", wt.LPWSTR),
        ("dwX", wt.DWORD), ("dwY", wt.DWORD), ("dwXSize", wt.DWORD), ("dwYSize", wt.DWORD),
        ("dwXCountChars", wt.DWORD), ("dwYCountChars", wt.DWORD), ("dwFillAttribute", wt.DWORD),
        ("dwFlags", wt.DWORD), ("wShowWindow", wt.WORD), ("cbReserved2", wt.WORD),
        ("lpReserved2", ctypes.c_void_p), ("hStdInput", wt.HANDLE), ("hStdOutput", wt.HANDLE),
        ("hStdError", wt.HANDLE),
    ]


class _PROCESS_INFORMATION(ctypes.Structure):
    _fields_ = [("hProcess", wt.HANDLE), ("hThread", wt.HANDLE), ("dwProcessId", wt.DWORD), ("dwThreadId", wt.DWORD)]


def launch_in_session(
    exe: str,
    args: str = "",
    session_id: Optional[int] = None,
    cwd: Optional[str] = None,
) -> dict:
    """
    Start a process on the interactive desktop of `session_id` (default: console session)
    using WTSQueryUserToken + DuplicateTokenEx + CreateProcessAsUserW.
    Requires the caller to be SYSTEM (i.e. the service install). Returns {started, pid, error}.
    """
    wtsapi32 = ctypes.WinDLL("wtsapi32.dll", use_last_error=True)
    advapi32 = ctypes.WinDLL("advapi32.dll", use_last_error=True)
    kernel32 = ctypes.WinDLL("kernel32.dll", use_last_error=True)
    userenv = ctypes.WinDLL("userenv.dll", use_last_error=True)

    kernel32.WTSGetActiveConsoleSessionId.restype = ctypes.c_uint32
    kernel32.CloseHandle.argtypes = [wt.HANDLE]
    wtsapi32.WTSQueryUserToken.argtypes = [wt.ULONG, ctypes.POINTER(wt.HANDLE)]
    advapi32.DuplicateTokenEx.argtypes = [
        wt.HANDLE, wt.DWORD, ctypes.c_void_p, ctypes.c_int, ctypes.c_int, ctypes.POINTER(wt.HANDLE)]
    userenv.CreateEnvironmentBlock.argtypes = [ctypes.POINTER(ctypes.c_void_p), wt.HANDLE, wt.BOOL]
    userenv.DestroyEnvironmentBlock.argtypes = [ctypes.c_void_p]
    advapi32.CreateProcessAsUserW.argtypes = [
        wt.HANDLE, wt.LPCWSTR, wt.LPWSTR, ctypes.c_void_p, ctypes.c_void_p, wt.BOOL, wt.DWORD,
        ctypes.c_void_p, wt.LPCWSTR, ctypes.POINTER(_STARTUPINFOW), ctypes.POINTER(_PROCESS_INFORMATION)]

    if session_id is None:
        session_id = kernel32.WTSGetActiveConsoleSessionId()
    if session_id == 0xFFFFFFFF:
        return {"started": False, "pid": None, "error": "No user is logged in on the console"}

    user_token = wt.HANDLE()
    if not wtsapi32.WTSQueryUserToken(session_id, ctypes.byref(user_token)):
        return {"started": False, "pid": None, "error": f"WTSQueryUserToken failed: {ctypes.get_last_error()}"}

    dup_token = wt.HANDLE()
    ok = advapi32.DuplicateTokenEx(user_token, 0xF01FF, None, 2, 1, ctypes.byref(dup_token))  # TOKEN_ALL_ACCESS, impersonation, primary
    kernel32.CloseHandle(user_token)
    if not ok:
        return {"started": False, "pid": None, "error": f"DuplicateTokenEx failed: {ctypes.get_last_error()}"}

    env_block = ctypes.c_void_p()
    userenv.CreateEnvironmentBlock(ctypes.byref(env_block), dup_token, False)

    si = _STARTUPINFOW()
    si.cb = ctypes.sizeof(si)
    si.lpDesktop = "winsta0\\default"
    pi = _PROCESS_INFORMATION()

    cmd_line = ctypes.create_unicode_buffer(f'"{exe}"' if not args else f'"{exe}" {args}')
    CREATE_UNICODE_ENVIRONMENT, NORMAL_PRIORITY_CLASS = 0x400, 0x20
    ok = advapi32.CreateProcessAsUserW(
        dup_token, exe, cmd_line, None, None, False,
        CREATE_UNICODE_ENVIRONMENT | NORMAL_PRIORITY_CLASS,
        env_block, cwd, ctypes.byref(si), ctypes.byref(pi),
    )
    last_err = ctypes.get_last_error()
    if env_block:
        userenv.DestroyEnvironmentBlock(env_block)
    kernel32.CloseHandle(dup_token)

    if not ok:
        return {"started": False, "pid": None, "error": f"CreateProcessAsUserW failed: {last_err}"}
    pid = pi.dwProcessId
    kernel32.CloseHandle(pi.hProcess)
    kernel32.CloseHandle(pi.hThread)
    return {"started": True, "pid": pid, "error": None}


def run_in_user_session(
    command: str,
    timeout: int = 30,
    capture_output: bool = True,
    session_id: Optional[int] = None,
) -> dict:
    """Run a PowerShell command on the interactive desktop. Returns {stdout, stderr, exit_code}."""
    if not capture_output:
        r = launch_in_session(
            PWSH, f"-ExecutionPolicy Bypass -NonInteractive -WindowStyle Hidden -Command {command}",
            session_id=session_id)
        return {"stdout": "", "stderr": r.get("error") or "", "exit_code": 0 if r["started"] else 1}

    out = Path(tempfile.mktemp(suffix=".txt"))
    script = Path(tempfile.mktemp(suffix=".ps1"))
    script.write_text(
        "$ErrorActionPreference='Continue'\n"
        "[Console]::OutputEncoding=[System.Text.Encoding]::UTF8\n"
        f"$_vmout = (& {{{command}}} 2>&1 | Out-String)\n"
        f'$_vmout | Out-File -FilePath "{out}" -Encoding utf8\n',
        encoding="utf-8-sig",
    )
    try:
        r = launch_in_session(
            PWSH, f'-ExecutionPolicy Bypass -NonInteractive -WindowStyle Hidden -File "{script}"',
            session_id=session_id)
        if not r["started"]:
            return {"stdout": "", "stderr": r.get("error", "launch failed"), "exit_code": 1}
        deadline = time.monotonic() + min(timeout, 60)
        while not out.exists() and time.monotonic() < deadline:  # file appears when the script finishes
            time.sleep(0.1)
        time.sleep(0.2)
        stdout = out.read_text(encoding="utf-8-sig", errors="replace").rstrip("\n") if out.exists() else ""
    finally:
        script.unlink(missing_ok=True)
        out.unlink(missing_ok=True)
    return {"stdout": stdout, "stderr": "", "exit_code": 0}
