"""mcp_instance.py — the single FastMCP object every tool module registers on.

Tool modules do:   from vmmcp.mcp_instance import mcp   and   @mcp.tool()
The agent-facing usage guide lives in guide.py so this file stays small.
"""

from __future__ import annotations

from fastmcp import FastMCP

from .guide import SERVER_INSTRUCTIONS

mcp = FastMCP(name="vm-mcp", instructions=SERVER_INSTRUCTIONS)
