# AGENTS.md — map of this repo (read this first)

vm-mcp = an MCP server that runs on a Windows machine and lets an AI use it: shell, files, registry,
services, network … and **computer use** (screenshots + mouse + keyboard) with a visible animated AI cursor.

## Where is what

```
src/
  server.py                 entry point only (3 lines). install scripts/services point here.
  requirements.txt
  vmmcp/
    __init__.py             VERSION  (bump it when computer/ changes: forces the desktop agent to restart)
    app.py                  builds the ASGI app, runs uvicorn
    config.py               ALL env vars + paths + constants
    guide.py                the usage guide the connected AI sees (MCP "instructions") — edit to change AI behaviour
    mcp_instance.py         the one FastMCP object
    security.py             pure-ASGI Gate: API key, IP allowlist, rate limit, SSE connect/disconnect tracking
    toolwrap.py             wraps EVERY tool: runs in a thread (never blocks the event loop), timeout, recording
    activity.py             event store (calls, screenshots, connections) -> dashboard
    webui/                  dashboard: app.py (raw ASGI backend), index.html, login.html  (served at /ui/)
    skill/SKILL.md          the agent playbook (served by tool vm_guide; copy in skills/vm-mcp/SKILL.md)
    winutil.py              run_ps, session helpers, launch_in_session (CreateProcessAsUser)
    logs.py
    tools/                  ONE FILE PER TOOL FAMILY — add a tool = add a function with @mcp.tool()
      computer.py           <- computer-use tools (screenshot, mouse_*, keyboard_*, ui_tree ...)
      shell.py files.py processes.py registry.py services.py network.py system.py
      software.py power.py clipboard.py server_admin.py knowledge.py (vm_guide)
    computer/               implementation behind tools/computer.py (see docs/ARCHITECTURE.md)
      client.py             SERVER side: starts/finds the desktop agent, coordinate scaling
      agent.py              AGENT side entry (runs on the user's desktop)
      actor.py              actions = cursor animation + real input
      overlay.py            Win32 click-through window showing the cursor
      cursor_engine.py      animation state machine + renderer  (restyle colours in MODES)
      cursor_art.py         cursor shape (VERTS/CUTS)
      motion.py inputs.py keys.py capture.py windows.py uia.py
skills/vm-mcp/SKILL.md      agent skill file (keep identical to src/vmmcp/skill/SKILL.md — a test checks)
assets/cursor/              cursor.svg, cursor-states.png, preview.gif (generated)
scripts/make_cursor_assets.py   regenerate assets from the real engine
tests/test_computer.py      runs anywhere (no Windows needed): python -m unittest discover -s tests -v
docs/                       ARCHITECTURE.md, CURSOR.md
*.ps1                       install / uninstall / tunnel / service helpers (Windows)
```

## Rules of the codebase
1. **A tool is a thin function.** Logic lives in a helper module, never inside the `@mcp.tool()` body of a big file.
2. **Agent-side modules** (`computer/` except `client.py`) may import only stdlib + Pillow. They run in a separate
   process that must not need fastmcp. Never import `config.py` secrets there.
3. **Coordinates:** tools speak *screenshot pixels*; `client.py` converts to real pixels; the agent works in real
   pixels relative to the desktop origin; `inputs.py` adds the virtual-desktop offset. Don't mix these spaces.
4. **Never block the event loop.** Tools are plain `def`s and are auto-wrapped (toolwrap.py) to run in threads — but
   never add `async def` tools that call blocking code, and never do blocking work in `security.py`/`webui/`
   without `asyncio.to_thread`.
5. **Never leave input stuck:** every press has a `finally` that releases it (see actor.drag / actor.hold).
6. Tool docstrings are what the AI reads. Keep them short, concrete, with examples.

## Common tasks
- *Change cursor look/colours:* `computer/cursor_engine.py → MODES`; shape: `cursor_art.py → VERTS, CUTS`;
  then `python scripts/make_cursor_assets.py`.
- *Add a computer-use action:* method in `actor.py` → handler in `agent.py::build_handlers` → tool in `tools/computer.py`.
- *Change what agents are told:* `src/vmmcp/skill/SKILL.md` (+ copy to `skills/vm-mcp/`), short form in `guide.py`.
- *Dashboard:* backend `webui/app.py`, UI `webui/index.html` (single file, no build step).
- *Add a non-visual tool:* function in the matching `tools/*.py`.
- *Release:* bump `VERSION`, run tests, push; on the VM call `server_update(restart=True)`.
