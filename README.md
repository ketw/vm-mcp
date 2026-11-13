# vm-mcp — Remote Windows VM Control + AI Computer Use via MCP  `v3.1`

A Python MCP server that runs on a Windows machine and lets any MCP client (Kiro CLI, Claude Desktop, your local
AI…) run commands **and see/operate the desktop** with a visible, animated AI cursor.

![AI cursor demo](assets/cursor/preview.gif)

```
 AI client ──HTTPS/SSE──▶ MCP server (service, Session 0) ──localhost──▶ Desktop agent (user's desktop)
                           67 tools                                      ├─ AI cursor overlay (90 fps)
                           shell/files/registry/…                        ├─ real mouse + keyboard (SendInput)
                                                                         └─ screenshots / UI tree
```

## Quick Start

On the VM (run as Administrator): `Set-ExecutionPolicy Bypass -Scope Process; .\install.ps1`
(no admin / own PC: `.\install-local.ps1 -StartNow`). Behind NAT? `.\tunnel.ps1` gives a public URL.

Add to your MCP client (`~/.kiro/settings/mcp.json` or equivalent):
```json
{ "mcpServers": { "vm-mcp": { "url": "http://<VM-IP>:8765/sse", "headers": { "X-Api-Key": "<key-from-install>" } } } }
```
Someone must be logged in on the VM console for computer use (it needs a desktop).

## Dashboard — watch the AI work

Open **`http://<server>:8765/ui/`** and sign in with your API key. You get:
* **The AI's eyes** — every screenshot it takes, full size, with a marker where it clicked/dragged, a filmstrip of recent
  screenshots, and a **Live view** toggle that streams the real desktop about once a second.
* **History** — every tool call: arguments, duration, result, error, and the screenshots it returned. Filter by
  Computer / Shell / Connection / Errors, search, click any row to pin that moment.
* **Connection log** — every MCP client connect and **disconnect with the reason** and how long it was up, plus rejected
  requests (bad key, rate limit). Logs tab shows the server and desktop-agent logs.

## Teach your agent: the skill file

`skills/vm-mcp/SKILL.md` is a ready-made skill/knowledge file (tool choice, the `screenshot_after` rule, coordinates,
workflows, troubleshooting, token checklist). Also served by the `vm_guide` tool, and the key rules are in the server's
built-in instructions. For Kiro CLI, copy it to `.kiro/steering/vm-mcp.md` in your workspace (or `~/.kiro/steering/`);
for Claude-style skills drop the `skills/vm-mcp/` folder into your skills directory.

## Computer use — 14 tools

Workflow for the AI: **screenshot once → act → put `screenshot_after=True` on the last action** (returns the settled result in the same call, no extra round-trip). Every coordinate is a pixel of the latest full screenshot.

| Tool | What it does |
|------|--------------|
| `screenshot` | Image of the screen. `grid=True` labels coordinates; `region=[x,y,w,h]` zooms; `hide_cursor` |
| `screen_info` | Text-only: screen size, cursor, front window, window list |
| `ui_tree` | Accessibility tree of the front window with exact click centres (works without vision) |
| `mouse_move` | Glide to x,y (hover) |
| `mouse_click` | left/right/middle, 1–3 clicks, optional modifiers (`["ctrl"]`) |
| `mouse_drag` | Press at A, move to B (optional waypoints), release |
| `mouse_hold` | Press and hold for N seconds |
| `mouse_button` | Low-level `down` / `up` for custom gestures |
| `mouse_scroll` | up/down/left/right, N notches |
| `keyboard_type` | Type text (Unicode-safe), optional Enter |
| `keyboard_key` | `enter`, `ctrl+a`, `alt+f4`, `ctrl+a ctrl+c` … with `repeat` |
| `window_focus` | Bring a window to front by title |
| `wait` | Pause with a "thinking" cursor pulse |
| `cursor_control` | status / show / hide / test the overlay |

The cursor is soft white and tints by action (blue click, violet right-click, teal double-click, amber drag/hold,
sky scroll, mint typing, lavender wait, coral error). Details: [docs/CURSOR.md](docs/CURSOR.md).
How it works: [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md).

## Other tools (53)

| Family | Tools |
|---|---|
| Shell | `shell_run` `shell_run_elevated` `shell_run_bg` `shell_run_interactive` |
| Files | `file_read` `file_write` `file_delete` `file_list` `file_copy` `file_move` `file_hash` `archive_create` `archive_extract` |
| Processes | `process_list` `process_start` `process_kill` |
| Clipboard | `clipboard_get` `clipboard_set` |
| Registry | `registry_read` `registry_write` `registry_delete` `registry_list` |
| Services & tasks | `service_status` `service_control` `service_list` `scheduled_task_list` `scheduled_task_run` |
| Network | `net_info` `net_ping` `net_port_scan` `net_connections` `firewall_rule_list` `firewall_rule_add` `firewall_rule_remove` |
| System | `vm_info` `event_log_read` `env_get` `env_set` `installed_drivers` `toast_notify` |
| Software | `winget_install` `winget_search` `winget_list` `download_file` `install_msi` |
| Server | `server_version` `server_update` `server_restart` `server_log_tail` `vm_guide` |
| Power | `reboot` `reboot_cancel` `shutdown` |

### Fixed in v3.1: "SSE drops / client stalls during mouse use"
Tools used to run *on* the server's asyncio loop. A slow tool (or an agent that failed to start, which waited 15 s per
call) froze the loop, SSE keep-alives stopped, and clients dropped the stream and never reconnected. Now every tool runs
in a worker thread with a hard timeout, agent start-up fails fast (≤ 8 s, cooldown, log excerpt in the error), localhost
calls ignore proxy env vars, and the SSE gate is plain ASGI instead of `BaseHTTPMiddleware`.

### Removed in v3.0
`screenshot` (old PowerShell version, blank from Session 0), `mouse_*`/`keyboard_*` (pyautogui versions) and all
`ai_*` tools (one PowerShell process per mouse move, 0.5–1.5 s each) — replaced by the 14 tools above, now backed by
one persistent desktop agent (~1 ms per input event). `ai_cursor.py` (Tk overlay) is replaced by the layered-window overlay.

---

## Self-Update

The server can update itself without touching the VM:

```
# From Kiro CLI — one-shot update + restart:
server_update(restart=True)

# Or two steps:
server_update()           ← downloads + unpacks new src/
server_restart()          ← re-execs the process with new code
```

`server_update` downloads the GitHub repo zip and replaces the whole `src/` tree (server.py + `vmmcp/` package).
The default URL is the `master` archive: `https://github.com/ketw/vm-mcp/archive/refs/heads/master.zip`.
Override it with `VM_MCP_UPDATE_URL`. The desktop agent is restarted automatically.

---

## Security

| Feature | Detail |
|---------|--------|
| API key | `X-Api-Key` header on every request (set via `VM_MCP_API_KEY`) |
| IP allowlist | `VM_MCP_ALLOWED_IPS=1.2.3.4,10.0.0.0/8` (optional) |
| Rate limit | `VM_MCP_RATE_LIMIT=60` requests/minute per IP (0 = off) |
| Health probe | `/health` and `/ping` require no auth |

---

## Network Options

| Option | Setup |
|--------|-------|
| Port forwarding | Forward TCP 8765 on router → VM local IP |
| **ngrok** (recommended for VMware NAT) | `.\tunnel.ps1 -AuthToken <token>` |
| Tailscale (best persistent) | Install Tailscale on both machines, use Tailscale IP |
| ZeroTier | Similar to Tailscale |

---

## Environment Variables

| Variable | Default | Description |
|----------|---------|-------------|
| `VM_MCP_API_KEY` | *(required)* | Secret API key |
| `VM_MCP_PORT` | `8765` | Listening port |
| `VM_MCP_HOST` | `0.0.0.0` | Bind address |
| `VM_MCP_SHELL_TIMEOUT` | `120` | Default shell timeout (seconds) |
| `VM_MCP_ALLOWED_IPS` | *(all)* | Comma-separated IP/CIDR allowlist |
| `VM_MCP_RATE_LIMIT` | `0` | Max requests/minute per IP (0=off) |
| `VM_MCP_UPDATE_URL` | GitHub master zip | Repo zip used by server_update() |
| `VM_MCP_AGENT_PORT` | `8788` | Localhost port of the desktop agent |
| `VM_MCP_SCREENSHOT_MAX_WIDTH` | `1920` | Screenshots wider than this are downscaled |
| `VM_MCP_AUTO_SCREENSHOT` | `0` | `1` = force a screenshot after every action (agents can also ask per call with `screenshot_after=True`) |
| `VM_MCP_CURSOR_PX` | `19` | AI cursor height in px at 100% scale (19 = stock Windows 11 arrow; auto-scales with display scaling and pointer size) |
| `VM_MCP_TOOL_TIMEOUT` | `150` | Seconds before a stuck tool call is abandoned with an error |
| `VM_MCP_JPEG_QUALITY` | `78` | Screenshot JPEG quality (lower = fewer tokens) |
| `VM_MCP_ACTIVITY_DIR` | `<install>\activity` | Where the dashboard history + screenshots are stored |
| `VM_MCP_LOG` | `<install_dir>/vm-mcp.log` | Log file path |

---

## Manual Run

```powershell
cd vm-mcp\src
pip install -r requirements.txt
$env:VM_MCP_API_KEY = "my-secret-key"
python server.py
```

---

## File Structure

See **[AGENTS.md](AGENTS.md)** for the full map. In short:

```
src/server.py            entry point            src/vmmcp/tools/      one file per tool family
src/vmmcp/computer/      cursor + vision + input   docs/               ARCHITECTURE.md, CURSOR.md
assets/cursor/           cursor.svg + previews     tests/               python -m unittest discover -s tests -v
```
