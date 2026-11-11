"""tools/software.py — Software install: winget, downloads, MSI/EXE."""

from __future__ import annotations

import tempfile
from pathlib import Path
from ..mcp_instance import mcp
from ..winutil import run_ps
from ..winutil import winget_path


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
    wg = winget_path()
    if not wg:
        return {"error": "winget.exe not found. Install App Installer from Microsoft Store.", "stdout": "", "exit_code": 1}
    return run_ps(f"& '{wg}' install --id {package_id}{flags}", timeout=300)


@mcp.tool()
def winget_search(query: str, limit: int = 20) -> dict:
    """
    Search the winget catalogue.

    Args:
        query: Search term.
        limit: Max results to return (default 20).

    Returns: {results: [{id, name, version, source}], count}
    """
    wg = winget_path()
    if not wg:
        return {"error": "winget.exe not found", "query": query}
    r = run_ps(f"& '{wg}' search '{query}' --accept-source-agreements 2>&1")
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
    wg = winget_path()
    if not wg:
        return {"error": "winget.exe not found", "stdout": "", "exit_code": 1}
    cmd = f"& '{wg}' list --accept-source-agreements"
    if filter_name:
        cmd += f" --name '{filter_name}'"
    return run_ps(cmd, timeout=60)

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
    r = run_ps(
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

    r = run_ps(cmd, timeout=timeout_seconds)
    if tmp: tmp.unlink(missing_ok=True)
    return {
        "success":             r["exit_code"] == 0,
        "installer_exit_code": r["stdout"].strip(),
        "stderr":              r["stderr"].strip(),
    }
