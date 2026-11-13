"""app.py — builds the ASGI app and runs it.  Entry point: `python server.py` (see src/server.py)."""

from __future__ import annotations

import sys

from . import VERSION
from .config import API_KEY, AGENT_PORT, HOST, LOG_FILE, PORT, UPDATE_URL
from .logs import setup_logging


def main() -> None:
    if sys.platform == "win32":
        for stream in (sys.stdout, sys.stderr):
            try:
                stream.reconfigure(encoding="utf-8", errors="replace")
            except Exception:
                pass
    if not API_KEY:
        sys.exit("ERROR: VM_MCP_API_KEY is not set.\nSet it first:  $env:VM_MCP_API_KEY = 'your-secret-key'\n")

    setup_logging()

    import uvicorn
    from starlette.applications import Starlette
    from starlette.routing import Mount

    from . import tools  # noqa: F401  (registers every tool)
    from .activity import store
    from .mcp_instance import mcp
    from .security import Gate
    from .webui.app import ui_app

    mcp_app = Starlette(routes=[Mount("/", app=mcp.http_app(transport="sse"))])
    app = Gate(mcp_app, ui_app)                          # auth + rate limit + dashboard + connection tracking
    store.note("system", f"vm-mcp {VERSION} started", event="start")
    print(f"""
vm-mcp {VERSION}
  Listening : {HOST}:{PORT}   (SSE: http://<ip>:{PORT}/sse, header X-Api-Key)
  Agent port: 127.0.0.1:{AGENT_PORT} (desktop agent, started on first computer-use call)
  Dashboard : http://<ip>:{PORT}/ui/   (sign in with the API key)
  Log       : {LOG_FILE}
  Update    : {UPDATE_URL}
""")
    uvicorn.run(app, host=HOST, port=PORT, log_level="info", timeout_graceful_shutdown=3)
