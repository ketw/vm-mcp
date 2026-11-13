"""tools/knowledge.py — `vm_guide`: lets any agent pull the full usage guide on demand."""

from __future__ import annotations

from pathlib import Path

from ..mcp_instance import mcp

SKILL_PATH = Path(__file__).resolve().parent.parent / "skill" / "SKILL.md"


@mcp.tool()
def vm_guide() -> str:
    """
    Returns the complete vm-mcp usage guide (which tool to pick, the `screenshot_after` rule, coordinate rules,
    standard workflows, troubleshooting, token-saving checklist). Read it once before a GUI task.
    """
    try:
        return SKILL_PATH.read_text(encoding="utf-8")
    except OSError as exc:
        return f"Guide file missing ({exc}). Key rule: pass screenshot_after=True on the LAST action of a sequence instead of calling screenshot() separately."
