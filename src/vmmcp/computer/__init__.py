"""vmmcp.computer — everything for AI computer-use (see docs/ARCHITECTURE.md).

Two halves that talk over localhost HTTP:

  SERVER SIDE (runs inside the MCP server, any session)
      client.py      launch/find the desktop agent, forward calls, coordinate scaling

  AGENT SIDE (runs on the user's interactive desktop; stdlib + Pillow + ctypes only)
      agent.py       HTTP endpoint + CLI entry:  python -m vmmcp.computer.agent
      actor.py       high-level actions (move/click/drag/type...) = cursor animation + real input
      overlay.py     Win32 click-through layered window that shows the AI cursor
      cursor_engine.py  animation state machine + frame renderer (pure Pillow, no Win32)
      cursor_art.py  cursor geometry + sprite masks (also exported as assets/cursor/cursor.svg)
      motion.py      easing, bezier paths, springs, colour maths (pure maths)
      inputs.py      SendInput mouse + keyboard
      keys.py        key-name parsing (pure)
      capture.py     screenshot, zoom, coordinate grid, encoding
      windows.py     list / focus top-level windows
      uia.py         accessibility tree (UI Automation) as text for non-vision models
"""
