"""guide.py — the short usage guide every MCP client shows the AI (FastMCP `instructions`).
The long version is skill/SKILL.md, served by the `vm_guide` tool. Keep THIS one tiny: it is sent on every session.
"""

SERVER_INSTRUCTIONS = """\
vm-mcp controls a Windows machine: shell, files, processes, registry, services, network, and visual computer use
(screen + mouse + keyboard) with an animated AI cursor. Call vm_guide() once for the full playbook.

CHEAPEST FIRST: shell_run / file_* / process_* / net_* beat the GUI. screen_info and ui_tree are text-only looks.
Use screenshot + mouse/keyboard only for GUI-only work.

COMPUTER USE LOOP
  1. screenshot()  once, at the start. All coordinates everywhere = pixels of the latest full screenshot.
  2. act: mouse_click / mouse_drag / mouse_scroll / keyboard_type / keyboard_key / window_focus / wait
  3. Put screenshot_after=True on the LAST action of a sequence. It returns the settled result in the same call.
     DO NOT call screenshot() right after an action — that wastes a round-trip. (Intermediate steps: leave it False.)
Precision: screenshot(region=[x,y,w,h], grid=True) zooms; grid labels stay in full-screen coordinates.
ui_tree() gives exact click centres without vision. keyboard_key takes 'ctrl+a', 'alt+f4', 'ctrl+a ctrl+c', repeat=N.
Cursor colours: white idle · blue click · violet right-click · teal double · amber drag/hold · sky scroll · mint typing ·
lavender wait · coral = error (read it before retrying).
Limits: lock screen, UAC prompts and ctrl+alt+del cannot be controlled. Tools time out after ~150 s.
"""
