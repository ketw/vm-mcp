---
name: vm-mcp
description: Operate a remote Windows machine through the vm-mcp MCP server — run PowerShell, manage files/processes/services/registry/network, and control the desktop visually (screenshots, mouse, keyboard) with an animated AI cursor. Use whenever a task involves the Windows VM, its GUI, or any app running on it.
---

# vm-mcp — how to use it well

Goal: finish tasks in the **fewest calls and fewest tokens**. Every call costs latency; every screenshot costs
image tokens. Pick the cheapest tool that can do the job.

## 1. Choose the right layer (cheapest first)

| Need | Use | Why |
|---|---|---|
| Anything scriptable: install, copy, read a file, query state, start/stop things | `shell_run`, `file_*`, `process_*`, `service_*`, `registry_*`, `net_*`, `winget_*` | Text in, text out. No screenshots. Fast. |
| Check what is on screen / which app is focused | `screen_info` | Text only, ~100 tokens |
| Find a button/field and its exact position, no image needed | `ui_tree` | Names + click centres as text |
| Genuinely visual or no API exists (GUI-only app, installer wizard, browser page) | `screenshot` + `mouse_*` / `keyboard_*` | The only option left |

**Never use the GUI for something a shell command can do.** Opening Settings to read the IP is wrong; `net_info` is right.

## 2. The golden rule: `screenshot_after=True`

Every ACT tool (`mouse_move`, `mouse_click`, `mouse_drag`, `mouse_hold`, `mouse_button`, `mouse_scroll`,
`keyboard_type`, `keyboard_key`, `window_focus`, `wait`) accepts **`screenshot_after`**.
Set it `True` and the same call returns the resulting screen (it waits until the UI stops changing first).

- ✅ `mouse_click(x=340, y=206, screenshot_after=True)` → 1 call, you see the result.
- ❌ `mouse_click(340, 206)` then `screenshot()` → 2 calls, 2 round-trips, and you may capture a half-drawn UI.

When to leave it `False`: intermediate steps where you do not need to see anything yet — e.g. clicking a text field
and then typing: `mouse_click(field)` (False) → `keyboard_type(text, screenshot_after=True)` (True).
Rule of thumb: **only the LAST action of a sequence needs the picture.**

Do not call `screenshot()` immediately after an action that could have returned one. Call `screenshot()` only to
(a) start a task, (b) zoom (`region=`), (c) add a `grid`, or (d) re-orient after something unexpected.

## 3. Coordinates

- Every x/y you send or receive is a pixel of the **latest full screenshot** (origin top-left). Never invent
  coordinates you have not seen.
- Small target? `screenshot(region=[x, y, w, h], grid=True)` returns a zoomed crop whose grid labels are **still in
  full-screen coordinates** — read the number off the grid and pass it straight to `mouse_click`. No conversion.
- Click the **centre** of buttons, not edges. For text fields, click inside the field, not the label.
- `ui_tree` gives `cx, cy` ready to click — most precise, works without vision.

## 4. Standard workflows

**Open an app and use it**
1. `shell_run("Start-Process notepad")`  (or `keyboard_key("win+r")` → type name → enter if GUI-only)
2. `wait(1, screenshot_after=True)`   ← you now see it; no separate screenshot
3. act on what you see, `screenshot_after=True` on the last action of each step

**Fill a form**
`mouse_click(field1)` → `keyboard_type("…")` → `keyboard_key("tab")` → `keyboard_type("…")` … → final action with
`screenshot_after=True`. Prefer `tab` between fields over clicking each one.

**Select / copy text**: `mouse_click(x,y, count=3)` selects a line; `keyboard_key("ctrl+a ctrl+c")` copies all.
Read the clipboard with `clipboard_get` instead of OCR-ing a screenshot.

**Drag & drop / sliders / resize**: one `mouse_drag(x1,y1,x2,y2)`. If the app ignores it, build it by hand:
`mouse_move(a)` → `mouse_button("down")` → `mouse_move(b)` → `mouse_button("up")`. Always release.

**Scroll to find something**: `mouse_scroll(x, y, "down", 5, screenshot_after=True)` — aim at the scrollable content.

**Switch windows**: `window_focus("Chrome", screenshot_after=True)`. Do not hunt for taskbar icons.

## 5. The AI cursor (what the human sees)

A soft-white cursor glides to each target and tints by action: blue click · violet right-click · teal double-click ·
amber drag/hold · sky scroll · mint typing · lavender waiting · **coral = error**. If it turns coral, the action failed —
read the error before retrying. `cursor_control("hide"|"show"|"test")` manages it; it still works when hidden.

## 6. When things go wrong

| Symptom | Do |
|---|---|
| Click had no effect | Window not focused → `window_focus(title)`; or UI still loading → `wait(1, screenshot_after=True)`; or you clicked a label → zoom with `region`+`grid` and re-aim |
| "desktop agent … never answered" | Read the log excerpt in the error. Usually Pillow missing for the server's Python. Do not retry in a loop; fix with `shell_run("pip install Pillow")` then retry once |
| "Nobody is logged in" | There is no desktop. Use shell/file tools, or ask the user to log in on the VM console |
| Lock screen / UAC prompt / Ctrl+Alt+Del | Cannot be controlled here. Ask the human |
| Elevated (Administrator) app ignores input | Input from a normal process is blocked by Windows. Use `shell_run_elevated` instead, or ask the human |
| Text typed into the wrong place | Click the field first; use `keyboard_key("ctrl+a")` before replacing existing text |
| A call timed out | The server abandoned it after ~150 s. Check state with `screen_info`/`process_list` before repeating a non-idempotent action |

## 7. Token discipline checklist

- [ ] Used a shell/file tool instead of the GUI where possible
- [ ] One `screenshot` at the start, then `screenshot_after=True` on final actions only
- [ ] Used `screen_info` / `ui_tree` instead of a screenshot when text was enough
- [ ] Used `region=` to zoom instead of a bigger full screenshot
- [ ] Lowered `max_width` (e.g. 1280) on `screenshot` when fine detail is not needed — coordinates still work
- [ ] Batched keys: `keyboard_key("ctrl+a ctrl+c")`, `keyboard_key("down", repeat=5)`
- [ ] Never polled with repeated `screenshot` calls — used `wait(n, screenshot_after=True)` once

## 8. Tool map (67 tools)

Computer use (14): `screenshot` `screen_info` `ui_tree` `mouse_move` `mouse_click` `mouse_drag` `mouse_hold`
`mouse_button` `mouse_scroll` `keyboard_type` `keyboard_key` `window_focus` `wait` `cursor_control`
Shell: `shell_run` `shell_run_elevated` `shell_run_bg` `shell_run_interactive`
Files: `file_read` `file_write` `file_delete` `file_list` `file_copy` `file_move` `file_hash` `archive_create` `archive_extract`
Processes/services: `process_list` `process_start` `process_kill` `service_status` `service_control` `service_list` `scheduled_task_list` `scheduled_task_run`
Registry/clipboard/env: `registry_read` `registry_write` `registry_delete` `registry_list` `clipboard_get` `clipboard_set` `env_get` `env_set`
Network: `net_info` `net_ping` `net_port_scan` `net_connections` `firewall_rule_list` `firewall_rule_add` `firewall_rule_remove`
System/software: `vm_info` `event_log_read` `installed_drivers` `toast_notify` `winget_install` `winget_search` `winget_list` `download_file` `install_msi`
Server: `server_version` `server_update` `server_restart` `server_log_tail` `vm_guide` · Power: `reboot` `reboot_cancel` `shutdown`

The human can watch everything live at `http://<server>:8765/ui/` (screenshots = your eyes, full call history, connection log).
