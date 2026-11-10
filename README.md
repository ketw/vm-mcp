# vm-mcp — Remote Windows VM Control via MCP  `v2.0`

A Python MCP server that runs on your Windows VM and exposes it as a rich set
of tools Kiro CLI (or any MCP client) can call from anywhere over the internet.

```
┌─────────────────────────────┐         ┌──────────────────────────────────┐
│   Your machine (Kiro CLI)   │  HTTPS  │   Windows VM (vm-mcp server)     │
│                             │  SSE    │                                  │
│  mcp.json → vm-mcp ─────────┼────────▶│  40 tools over HTTP/SSE          │
│                             │         │  ├─ Shell (run, elevated, bg)    │
│  "run nemo status on VM"    │         │  ├─ Files (read/write/hash/zip)  │
│  "take a screenshot"        │         │  ├─ Processes (list/start/kill)  │
│  "install this .exe"        │         │  ├─ UI (screenshot/mouse/keys)   │
│  "read the registry"        │         │  ├─ Registry (read/write/list)   │
│  "port-scan 192.168.1.x"    │         │  ├─ Services (list/start/stop)   │
│  "update the server itself" │         │  ├─ Network (ping/scan/firewall) │
└─────────────────────────────┘         │  └─ Self-update + restart        │
                                        └──────────────────────────────────┘
```

---

## Quick Start

### On the VM (run as Administrator)

```powershell
Set-ExecutionPolicy Bypass -Scope Process
.\install.ps1
```

> **Behind NAT** (VMware Workstation default)? Run `.\tunnel.ps1` for a free public URL.

### Add to Kiro CLI

`~/.kiro/settings/mcp.json`:
```json
{
  "mcpServers": {
    "vm-mcp": {
      "url": "http://<VM-IP>:8765/sse",
      "headers": { "X-Api-Key": "<key-from-install>" }
    }
  }
}
```

---

## Tools — All 40

### Shell & Execution
| Tool | Description |
|------|-------------|
| `shell_run` | Run PowerShell → stdout / stderr / exit_code |
| `shell_run_elevated` | Run as SYSTEM via scheduled-task (bcdedit, driver ops) |
| `shell_run_bg` | Fire-and-forget background process (no wait) |
| `winget_install` | Install software via winget package manager |
| `winget_search` | Search the winget catalogue |
| `winget_list` | List installed packages |

### File System
| Tool | Description |
|------|-------------|
| `file_read` | Read file (text or base64 binary) |
| `file_write` | Write / create file (text or base64 binary) |
| `file_delete` | Delete file or directory |
| `file_list` | List directory (recursive, glob pattern) |
| `file_copy` | Copy file or directory tree |
| `file_move` | Move / rename file or directory |
| `file_hash` | SHA256 / MD5 / SHA1 hash of a file |
| `archive_create` | Zip or tar.gz files/directories |
| `archive_extract` | Extract zip / tar / 7z archive |

### Processes
| Tool | Description |
|------|-------------|
| `process_list` | List processes (sortable by memory, CPU, name, pid) |
| `process_start` | Launch a new process |
| `process_kill` | Kill process by PID or name |

### UI & Desktop
| Tool | Description |
|------|-------------|
| `screenshot` | Capture desktop (or region) as base64 PNG |
| `mouse_move` | Move mouse to screen coordinates |
| `mouse_click` | Left / right / double click at x,y |
| `keyboard_type` | Type a string with simulated keystrokes |
| `keyboard_hotkey` | Send hotkey: ctrl+c, win+d, alt+F4, etc. |
| `toast_notify` | Show Windows toast notification on the VM |

### Clipboard
| Tool | Description |
|------|-------------|
| `clipboard_get` | Read clipboard text |
| `clipboard_set` | Write clipboard text |

### Registry
| Tool | Description |
|------|-------------|
| `registry_read` | Read a value |
| `registry_write` | Write a value (REG_SZ/DWORD/QWORD/BINARY/MULTI_SZ) |
| `registry_delete` | Delete a value or entire key |
| `registry_list` | List subkeys and values in a key |

### Windows Services
| Tool | Description |
|------|-------------|
| `service_status` | Get single service status |
| `service_control` | Start / stop / restart / pause / resume |
| `service_list` | List all services (filterable by name and status) |

### Networking
| Tool | Description |
|------|-------------|
| `net_info` | Adapters, IPs, gateway, DNS, public IP |
| `net_ping` | Ping a host, return latency stats |
| `net_port_scan` | TCP port scan on a host |
| `net_connections` | Active TCP connections (filterable by state) |
| `firewall_rule_list` | List Windows Firewall rules |
| `firewall_rule_add` | Add a firewall rule |
| `firewall_rule_remove` | Remove a firewall rule by name |

### System
| Tool | Description |
|------|-------------|
| `vm_info` | OS, CPU, RAM, disk, uptime, public IP |
| `event_log_read` | Read Windows Event Log (System/Application/Security) |
| `scheduled_task_list` | List scheduled tasks |
| `scheduled_task_run` | Run a scheduled task immediately |
| `env_get` | Read an environment variable |
| `env_set` | Set / delete a system environment variable |
| `installed_drivers` | List installed kernel-mode drivers |

### Software
| Tool | Description |
|------|-------------|
| `download_file` | Download URL to local path |
| `install_msi` | Install MSI / EXE silently (URL or local) |

### Server Self-Management
| Tool | Description |
|------|-------------|
| `server_version` | Current version, path, PID, uptime |
| `server_update` | Fetch latest server.py from GitHub and replace |
| `server_restart` | Re-exec the server process (applies update) |
| `server_log_tail` | Tail the server log file |

### Power
| Tool | Description |
|------|-------------|
| `reboot` | Schedule VM reboot (with delay) |
| `reboot_cancel` | Cancel pending reboot |
| `shutdown` | Schedule VM shutdown |

---

## Self-Update

The server can update itself without touching the VM:

```
# From Kiro CLI — one-shot update + restart:
server_update(restart=True)

# Or two steps:
server_update()           ← downloads new server.py
server_restart()          ← re-execs the process with new code
```

The update URL defaults to:
```
https://raw.githubusercontent.com/ketw/vm-mcp/master/src/server.py
```
Override it by setting `VM_MCP_UPDATE_URL` on the VM before install.

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
| `VM_MCP_UPDATE_URL` | GitHub raw URL | URL for server_update() |
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

```
vm-mcp/
├── src/
│   ├── server.py          ← MCP server (40 tools)
│   └── requirements.txt   ← Python dependencies
├── install.ps1            ← One-shot installer (run on VM as admin)
├── tunnel.ps1             ← ngrok tunnel for VMs behind NAT
├── uninstall.ps1          ← Full removal
└── README.md              ← This file
```
