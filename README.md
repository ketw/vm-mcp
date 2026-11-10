# vm-mcp — Remote Windows VM Control via MCP

A Python MCP server that runs on your Windows VM and exposes it as a set of
tools Kiro CLI (or any MCP client) can call from anywhere over the internet.

```
┌─────────────────────────────┐         ┌──────────────────────────────┐
│   Your machine (Kiro CLI)   │         │   Windows VM                 │
│                             │  HTTPS  │                              │
│  mcp.json → vm-mcp ─────────┼────────▶│  server.py (port 8765)       │
│                             │  SSE    │  ├─ shell_run                │
│  "run this command on VM"   │         │  ├─ file_read / file_write   │
│  "install this on VM"       │         │  ├─ screenshot               │
│  "take a screenshot"        │         │  ├─ service_control          │
│  "read this registry key"   │         │  ├─ install_msi              │
└─────────────────────────────┘         │  └─ ... 18 tools total       │
                                        └──────────────────────────────┘
```

---

## Quick Start (5 minutes)

### On the VM

```powershell
# 1. Copy vm-mcp folder to the VM (shared folder or file transfer)
# 2. Open elevated PowerShell, navigate to the vm-mcp folder
Set-ExecutionPolicy Bypass -Scope Process

# 3. Run the installer
.\install.ps1
```

The installer will:
- Install Python if not present (via winget)
- Install all Python dependencies
- Generate a random API key
- Register vm-mcp as a Windows service (auto-starts on boot)
- Open firewall port 8765

> **If the VM is behind NAT** (VMware Workstation default, home router): run
> `.\tunnel.ps1` after install to get a free public HTTPS URL via ngrok.

### On your machine (Kiro CLI)

Add to `~/.kiro/settings/mcp.json`:

```json
{
  "mcpServers": {
    "vm-mcp": {
      "url": "http://<VM-PUBLIC-IP>:8765/sse",
      "headers": {
        "X-Api-Key": "<your-api-key-from-install>"
      }
    }
  }
}
```

If you used ngrok, the URL will be `https://<random>.ngrok-free.app/sse`.

---

## Tools Reference

| Tool | Description |
|------|-------------|
| `shell_run` | Run a PowerShell command; returns stdout/stderr/exit_code |
| `shell_run_elevated` | Run as SYSTEM via scheduled task (for bcdedit, driver ops) |
| `file_read` | Read a file (text or base64 binary) |
| `file_write` | Write / create a file (text or base64 binary) |
| `file_delete` | Delete a file or directory |
| `file_list` | List directory contents with metadata |
| `process_list` | List running processes (filterable by name) |
| `process_kill` | Kill a process by PID or name |
| `screenshot` | Capture desktop as base64-encoded PNG |
| `clipboard_get` | Read clipboard text |
| `clipboard_set` | Write text to clipboard |
| `registry_read` | Read a registry key value |
| `registry_write` | Write a registry key value |
| `service_status` | Get Windows service status |
| `service_control` | Start / stop / restart a service |
| `download_file` | Download a URL to the VM filesystem |
| `install_msi` | Download and install an MSI/EXE silently |
| `vm_info` | OS, hostname, IPs, RAM, disk, uptime |
| `reboot` | Schedule a VM reboot (with delay) |
| `reboot_cancel` | Cancel a pending reboot |

---

## Example Kiro CLI Interactions

Once connected, you can tell Kiro things like:

```
"Take a screenshot of the VM and show me what's on the desktop"
→ screenshot()

"Install NemoSetup-1.0.0.exe from C:\Users\User\Desktop on the VM"
→ shell_run("C:\\Users\\User\\Desktop\\NemoSetup-1.0.0.exe /quiet /norestart")

"Check if NemoSvc is running on the VM"
→ service_status("NemoSvc")

"Start NemoSvc on the VM"
→ service_control("NemoSvc", "start")

"Run nemo status on the VM"
→ shell_run("nemo status")

"Read the Nemo boot policy registry key"
→ registry_read("HKLM\\SYSTEM\\CurrentControlSet\\Services\\NemoKmd\\BootPolicy")

"Download the latest Nemo installer to the VM and install it"
→ download_file("https://...", "C:\\Temp\\NemoSetup.exe")
→ install_msi("C:\\Temp\\NemoSetup.exe", is_url=False)

"Show me disk space on the VM"
→ vm_info()

"Reboot the VM in 30 seconds"
→ reboot(delay_seconds=30)
```

---

## Security

- Every request requires the `X-Api-Key` header matching `VM_MCP_API_KEY`
- Optional IP allowlist: set `VM_MCP_ALLOWED_IPS=1.2.3.4,10.0.0.0/8` to restrict by IP
- Keep the API key secret — it gives full shell access to the VM
- Use ngrok or a VPN for the network path rather than exposing port 8765 directly
- The server runs as SYSTEM (service) so it has full machine access

---

## Network Options

### Option A: Port forwarding (physical machine / static IP)
Forward TCP port 8765 on your router/firewall to the VM's local IP.
Use `http://<public-ip>:8765/sse` in mcp.json.

### Option B: ngrok tunnel (recommended for VMware NAT / dynamic IP)
```powershell
.\tunnel.ps1 -AuthToken <your-ngrok-token>
```
Free ngrok account: https://dashboard.ngrok.com

### Option C: Tailscale / ZeroTier (best for persistent access)
Install Tailscale on both the VM and your machine. Use the VM's Tailscale IP:
```powershell
winget install Tailscale.Tailscale
```
Then use `http://<tailscale-ip>:8765/sse` — works with no port forwarding, no ngrok.

---

## File Structure

```
vm-mcp/
├── src/
│   ├── server.py          ← MCP server (the actual code)
│   └── requirements.txt   ← Python dependencies
├── install.ps1            ← One-shot installer (run on VM as admin)
├── tunnel.ps1             ← ngrok tunnel launcher
├── uninstall.ps1          ← Full removal script
└── README.md              ← This file
```

---

## Manual Run (without installing as service)

```powershell
cd vm-mcp\src
pip install -r requirements.txt
$env:VM_MCP_API_KEY = "my-secret-key-here"
$env:VM_MCP_PORT    = "8765"
python server.py
```

---

## Troubleshooting

| Problem | Solution |
|---------|----------|
| `VM_MCP_API_KEY not set` | Set the env var before starting: `$env:VM_MCP_API_KEY = "..."` |
| Connection refused | Check firewall: `Get-NetFirewallRule -DisplayName "VmMcpServer"` |
| 401 Unauthorized | Wrong API key in mcp.json `headers` |
| 403 Forbidden | Your IP not in `VM_MCP_ALLOWED_IPS` |
| Service won't start | Check log: `Get-Content C:\VmMcp\vm-mcp-err.log -Tail 20` |
| Can't connect from internet | VM is behind NAT — use `tunnel.ps1` or port forward |
| `Import error: PIL` | `pip install Pillow` on the VM |
| `Import error: psutil` | `pip install psutil` on the VM |
