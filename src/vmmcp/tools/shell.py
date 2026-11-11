"""tools/shell.py — Shell & execution."""

from __future__ import annotations

import subprocess
from ..config import PWSH
from ..config import SHELL_TIMEOUT
from ..mcp_instance import mcp
from ..winutil import run_in_user_session
from ..winutil import run_ps


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
    return run_ps(command, timeout=min(timeout_seconds, 120), elevated=True)


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
    return run_in_user_session(
        command,
        timeout=min(timeout_seconds, 120),
        capture_output=capture_output,
    )
