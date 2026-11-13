"""mcp_instance.py — the single FastMCP object every tool module registers on.

Tool modules do:   from vmmcp.mcp_instance import mcp   and   @mcp.tool()
`mcp.tool` is patched here so every tool is automatically run off the event loop, time-limited and recorded
(see toolwrap.py). The agent-facing usage guide lives in guide.py.
"""

from __future__ import annotations

from fastmcp import FastMCP

from .guide import SERVER_INSTRUCTIONS
from .toolwrap import wrap

mcp = FastMCP(name="vm-mcp", instructions=SERVER_INSTRUCTIONS)

_original_tool = mcp.tool


def _tool(*args, **kwargs):
    if args and callable(args[0]):                       # used as bare @mcp.tool
        return _original_tool(wrap(args[0]))
    register = _original_tool(*args, **kwargs)
    return lambda fn: register(wrap(fn))


mcp.tool = _tool
