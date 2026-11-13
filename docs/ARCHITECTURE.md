# Architecture

## Why two processes?
Windows services (the NSSM install runs as SYSTEM) live in **Session 0**, which has no desktop: they cannot
see the screen, draw windows, or inject input into what the user sees. So computer use is split:

```
MCP client (your AI)
      │ HTTPS/SSE, X-Api-Key
      ▼
┌──────────────────────────────┐          localhost HTTP + token          ┌─────────────────────────────────────┐
│ MCP server  (Session 0 / any)│ ───────────────────────────────────────▶ │ Desktop agent (user's desktop)      │
│  tools/computer.py           │                                          │  agent.py  → actor.py               │
│  computer/client.py          │ ◀─────────────────────────────────────── │  overlay.py (cursor window, 90 fps) │
└──────────────────────────────┘             JSON / base64 image          │  inputs.py (SendInput)              │
        │ CreateProcessAsUser (service)                                    │  capture.py (screenshots)           │
        └─ or plain Popen (user-mode install) ───────── launches ────────▶ └─────────────────────────────────────┘
```
* `client.ensure_agent()` runs on every computer-use call: ping `127.0.0.1:8788`; if the agent is missing, has an
  old `VERSION`, or sits in the wrong session (user switched), it is (re)started on the active console session.
* The agent token is `sha256("vmmcp-agent:" + API_KEY)`; the agent only listens on 127.0.0.1.
* Agent log: `%TEMP%\vm-mcp-agent.log` (of the logged-in user).

## One action, end to end — `mouse_click(x, y)`
1. `tools/computer.py` converts screenshot px → real px (`client.to_screen`), POSTs `/click`.
2. `actor.click` → `overlay.glide`: the cursor engine moves along a bezier arc (0.2–0.9 s, eased); **every frame**
   it calls `inputs.move_abs`, so the real mouse follows the visible cursor.
3. Arrived → `inputs.snap` (exact pixel) → mode `click` (blue) → press squash + `SendInput` down/up → ripple.
4. Reply `{clicked, x, y}` → converted back to screenshot px.

## The cursor
* `cursor_art.py` shape → masks (body, shadow, glow, hairline) rendered once at 2× supersampling.
* `cursor_engine.CursorEngine.step(dt)` advances springs/tweens; `render()` affine-transforms the masks
  (rotate/scale about the tip so the hotspot never moves), tints, composites → premultiplied BGRA.
* `overlay.py` blits that with `UpdateLayeredWindow` (per-pixel alpha). Window styles: layered, transparent
  (click-through), topmost, no-activate, tool-window (no taskbar/alt-tab entry).
* Idle = zero CPU: frames are only rendered when something changed.

## Vision
* `screenshot` — PIL `ImageGrab` of the whole virtual desktop incl. layered windows (so the AI cursor is visible
  in the image). Returned as a real MCP image. `grid=True` adds labelled lines; `region=` zooms.
* `ui_tree` — UI Automation tree (names, types, click centres) for non-vision models / pixel-perfect targets.
* `screen_info` — text-only: screen size, cursor, front window, window list.

## Coordinate spaces
| Space | Used by | Origin |
|---|---|---|
| screenshot px | AI-facing tools | top-left of latest full screenshot |
| real px (relative) | `agent.py`, `actor.py` | top-left of virtual desktop |
| absolute px | `inputs.py`, `overlay.py` | Windows virtual-desktop origin (may be negative) |

`scale = screen_width / min(screen_width, VM_MCP_SCREENSHOT_MAX_WIDTH)` — exactly 1.0 for screens ≤ 1920 wide.

## Keeping the MCP connection alive
FastMCP runs plain sync tools directly on the asyncio event loop. Anything that blocks it (a 0.8 s mouse glide, a 15 s
agent start-up wait) stops the SSE keep-alive pings; clients then drop the stream and often never reconnect. So:
`toolwrap.wrap()` runs every tool via `asyncio.to_thread` with `VM_MCP_TOOL_TIMEOUT`, `client.ensure_agent()` fails fast
with a cooldown, `security.Gate` is pure ASGI (no `BaseHTTPMiddleware` around the stream) and logs every stream
open/close with its reason to the dashboard.

## Dashboard (`/ui/`)
`activity.store` records each tool call (args redacted/truncated, duration, result summary, returned screenshots saved
to `activity/img/`) and connection events. `webui/app.py` serves it as plain JSON (`/ui/api/events` polled every 700 ms;
changes to still-running calls are re-sent so rows complete). Auth = HttpOnly cookie derived from the API key.
`/ui/api/live` asks the agent for a low-res frame but never starts the agent.

## Known limits
Lock screen, UAC secure desktop and Ctrl+Alt+Del can't be captured or driven. Apps running as Administrator ignore
input from a non-elevated process (run the agent elevated if you need that). The real mouse is shared: if a human
moves it mid-action, the AI's action will be disturbed.
