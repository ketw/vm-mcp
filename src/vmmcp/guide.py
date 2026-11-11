"""guide.py — the usage guide every MCP client shows to the AI (FastMCP `instructions`)."""

SERVER_INSTRUCTIONS = """\
vm-mcp controls a Windows machine: shell, files, processes, registry, services, network, and a visual
COMPUTER-USE toolset (screen + mouse + keyboard) with a visible animated AI cursor.

COMPUTER USE — how to work
  1. screenshot()            look first. Coordinates in EVERY tool = pixels of the latest full screenshot,
                             origin top-left. Never guess coordinates you have not seen.
  2. act                     mouse_click / mouse_drag / mouse_scroll / keyboard_type / keyboard_key ...
  3. screenshot() again      verify the result before the next step. UIs lag: wait(1) if something is loading.
Precision tips
  - screenshot(grid=True) draws labelled coordinate lines; screenshot(region=[x,y,w,h], grid=True) zooms in
    for small targets. Labels and all tool coordinates stay in full-screenshot space (no conversion).
  - ui_tree() lists on-screen controls of the front window with exact click centres (cx, cy). Use it when the
    screenshot is ambiguous or you have no vision.
  - screen_info() is a cheap text-only look: screen size, cursor, front window, window list.
  - To focus a window use window_focus(title) rather than hunting for taskbar icons.
  - Click a text field before keyboard_type. keyboard_key takes 'enter', 'ctrl+a', 'alt+f4', 'win+r',
    'ctrl+shift+t', or several chords: 'ctrl+a ctrl+c'.
  - Drag = mouse_drag(x1,y1,x2,y2). For manual control: mouse_move, mouse_button('down'), mouse_move, mouse_button('up').
The AI cursor changes colour by action (white idle, blue click, violet right-click, teal double-click, amber
drag/hold, sky scroll, mint typing, lavender waiting, coral error) — use it to see what the system thinks it is doing.
Limits: the Windows lock screen, UAC secure prompts and ctrl+alt+del cannot be controlled from here.

OTHER TOOLS (non-visual, prefer these when no GUI is needed)
  shell_*  PowerShell        file_* / archive_*  files        process_*  processes     registry_*  registry
  service_* / scheduled_task_*  services & tasks               net_* / firewall_*  network
  vm_info / event_log_read / env_*  system                     winget_* / download_file / install_msi  software
  server_*  version, self-update, restart, logs                reboot / shutdown  power
Prefer shell_run for anything scriptable; use computer-use only for things that truly need a GUI.
"""
