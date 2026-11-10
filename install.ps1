# =============================================================================
# install.ps1 — Install vm-mcp as a persistent Windows service
#
# Run from an elevated PowerShell session on the VM:
#   Set-ExecutionPolicy Bypass -Scope Process
#   .\install.ps1
#
# What this does:
#   1. Checks/installs Python 3.11+ via winget
#   2. pip installs all dependencies
#   3. Generates a random API key (or lets you supply one)
#   4. Creates a Windows service via NSSM (or native sc.exe wrapper)
#   5. Opens the firewall port
#   6. Optionally starts ngrok for a public tunnel URL
# =============================================================================

#Requires -RunAsAdministrator
$ErrorActionPreference = "Stop"

$INSTALL_DIR = "C:\VmMcp"
$SERVICE_NAME = "VmMcpServer"
$PORT = 8765
$SCRIPT_DIR = Split-Path -Parent $MyInvocation.MyCommand.Definition

Write-Host ""
Write-Host "=====================================================" -ForegroundColor Cyan
Write-Host "  Nemo VM MCP Server — Installer" -ForegroundColor Cyan
Write-Host "=====================================================" -ForegroundColor Cyan
Write-Host ""

# ─── Step 1: Python ──────────────────────────────────────────────────────────
Write-Host "[1/7] Checking Python..." -ForegroundColor Yellow
$pythonCmd = Get-Command python -ErrorAction SilentlyContinue
if (-not $pythonCmd) {
    Write-Host "  Python not found — installing via winget..."
    winget install --id Python.Python.3.12 --silent --accept-package-agreements --accept-source-agreements
    # Refresh PATH
    $env:PATH = [System.Environment]::GetEnvironmentVariable("PATH", "Machine") + ";" +
                [System.Environment]::GetEnvironmentVariable("PATH", "User")
    $pythonCmd = Get-Command python -ErrorAction SilentlyContinue
    if (-not $pythonCmd) {
        Write-Host "ERROR: Python still not found after install. Close and reopen an elevated prompt." -ForegroundColor Red
        exit 1
    }
}
$pyVer = & python --version
Write-Host "  Found: $pyVer" -ForegroundColor Green

# ─── Step 2: Copy server files ───────────────────────────────────────────────
Write-Host "[2/7] Copying server files to $INSTALL_DIR..." -ForegroundColor Yellow
New-Item -ItemType Directory -Force -Path $INSTALL_DIR | Out-Null
Copy-Item -Path "$SCRIPT_DIR\src\*" -Destination $INSTALL_DIR -Recurse -Force
Write-Host "  Copied." -ForegroundColor Green

# ─── Step 3: Install Python dependencies ─────────────────────────────────────
Write-Host "[3/7] Installing Python dependencies..." -ForegroundColor Yellow
& python -m pip install --upgrade pip --quiet
& python -m pip install -r "$INSTALL_DIR\requirements.txt" --quiet
if ($LASTEXITCODE -ne 0) {
    Write-Host "ERROR: pip install failed." -ForegroundColor Red; exit 1
}
Write-Host "  Dependencies installed." -ForegroundColor Green

# ─── Step 4: API key ─────────────────────────────────────────────────────────
Write-Host "[4/7] Setting up API key..." -ForegroundColor Yellow

$existingKey = [System.Environment]::GetEnvironmentVariable("VM_MCP_API_KEY", "Machine")
if ($existingKey) {
    Write-Host "  Existing API key found in system environment." -ForegroundColor Cyan
    $keepExisting = Read-Host "  Keep existing key? [Y/n]"
    if ($keepExisting -ne "n" -and $keepExisting -ne "N") {
        $API_KEY = $existingKey
    } else {
        $existingKey = $null
    }
}

if (-not $existingKey) {
    $customKey = Read-Host "  Enter API key (press Enter to auto-generate)"
    if ($customKey) {
        $API_KEY = $customKey
    } else {
        # Generate a 64-char hex key
        $API_KEY = ([System.Guid]::NewGuid().ToString("N") + [System.Guid]::NewGuid().ToString("N")).ToUpper()
        Write-Host "  Generated API key: $API_KEY" -ForegroundColor Green
        Write-Host "  SAVE THIS KEY — you need it to connect from Kiro CLI." -ForegroundColor Yellow
    }
}

# Persist to system environment (available to the service)
[System.Environment]::SetEnvironmentVariable("VM_MCP_API_KEY", $API_KEY, "Machine")
[System.Environment]::SetEnvironmentVariable("VM_MCP_PORT",    "$PORT",   "Machine")
Write-Host "  API key saved to system environment." -ForegroundColor Green

# ─── Step 5: Create startup wrapper ──────────────────────────────────────────
Write-Host "[5/7] Creating service startup script..." -ForegroundColor Yellow

$startScript = @"
@echo off
:: vm-mcp service startup wrapper
set VM_MCP_API_KEY=$API_KEY
set VM_MCP_PORT=$PORT
set VM_MCP_HOST=0.0.0.0
python "$INSTALL_DIR\server.py" >> "$INSTALL_DIR\vm-mcp.log" 2>&1
"@
$startScript | Out-File -FilePath "$INSTALL_DIR\start.bat" -Encoding ASCII
Write-Host "  Startup script: $INSTALL_DIR\start.bat" -ForegroundColor Green

# ─── Step 6: Register as Windows service ─────────────────────────────────────
Write-Host "[6/7] Registering Windows service '$SERVICE_NAME'..." -ForegroundColor Yellow

# Try NSSM first (best option — handles stdout/stderr)
$nssmPath = Get-Command nssm -ErrorAction SilentlyContinue
if (-not $nssmPath) {
    Write-Host "  NSSM not found — attempting to install via winget..."
    winget install --id NSSM.NSSM --silent --accept-package-agreements --accept-source-agreements 2>$null
    $nssmPath = Get-Command nssm -ErrorAction SilentlyContinue
}

$existingSvc = Get-Service -Name $SERVICE_NAME -ErrorAction SilentlyContinue
if ($existingSvc) {
    Write-Host "  Service already exists — stopping and removing..."
    Stop-Service -Name $SERVICE_NAME -Force -ErrorAction SilentlyContinue
    if ($nssmPath) {
        & nssm remove $SERVICE_NAME confirm 2>$null
    } else {
        sc.exe delete $SERVICE_NAME 2>$null
    }
    Start-Sleep 2
}

$pythonExe = (Get-Command python).Source
if ($nssmPath) {
    Write-Host "  Using NSSM..."
    & nssm install $SERVICE_NAME $pythonExe "$INSTALL_DIR\server.py"
    & nssm set $SERVICE_NAME AppDirectory $INSTALL_DIR
    & nssm set $SERVICE_NAME AppEnvironmentExtra `
        "VM_MCP_API_KEY=$API_KEY" `
        "VM_MCP_PORT=$PORT" `
        "VM_MCP_HOST=0.0.0.0"
    & nssm set $SERVICE_NAME AppStdout "$INSTALL_DIR\vm-mcp.log"
    & nssm set $SERVICE_NAME AppStderr "$INSTALL_DIR\vm-mcp-err.log"
    & nssm set $SERVICE_NAME AppRotateFiles 1
    & nssm set $SERVICE_NAME AppRotateBytes 10485760   # 10 MB rotate
    & nssm set $SERVICE_NAME Start SERVICE_AUTO_START
    & nssm set $SERVICE_NAME Description "Nemo VM MCP remote control server"
} else {
    Write-Host "  NSSM not available — using sc.exe + scheduled task fallback..."
    # Create a VBS launcher that hides the console window
    $vbs = @"
Set WshShell = CreateObject("WScript.Shell")
WshShell.Run "cmd /c set VM_MCP_API_KEY=$API_KEY && set VM_MCP_PORT=$PORT && python $INSTALL_DIR\server.py >> $INSTALL_DIR\vm-mcp.log 2>&1", 0, False
"@
    $vbs | Out-File -FilePath "$INSTALL_DIR\start.vbs" -Encoding ASCII

    # Register as a scheduled task that runs at startup as SYSTEM
    $action  = New-ScheduledTaskAction -Execute "wscript.exe" -Argument "`"$INSTALL_DIR\start.vbs`""
    $trigger = New-ScheduledTaskTrigger -AtStartup
    $principal = New-ScheduledTaskPrincipal -UserId "SYSTEM" -LogonType ServiceAccount -RunLevel Highest
    $settings = New-ScheduledTaskSettingsSet -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries -StartWhenAvailable -RestartCount 5 -RestartInterval (New-TimeSpan -Minutes 1)
    Register-ScheduledTask -TaskName $SERVICE_NAME -Action $action -Trigger $trigger -Principal $principal -Settings $settings -Force | Out-Null
    Write-Host "  Registered as Scheduled Task '$SERVICE_NAME' (runs at startup as SYSTEM)." -ForegroundColor Green
}

# ─── Step 7: Firewall rule ────────────────────────────────────────────────────
Write-Host "[7/7] Opening firewall port $PORT..." -ForegroundColor Yellow
$existingRule = Get-NetFirewallRule -DisplayName "VmMcpServer" -ErrorAction SilentlyContinue
if ($existingRule) { Remove-NetFirewallRule -DisplayName "VmMcpServer" }
New-NetFirewallRule `
    -DisplayName "VmMcpServer" `
    -Direction Inbound `
    -Protocol TCP `
    -LocalPort $PORT `
    -Action Allow `
    -Profile Any | Out-Null
Write-Host "  Firewall rule created for port $PORT." -ForegroundColor Green

# ─── Start the service now ────────────────────────────────────────────────────
Write-Host ""
Write-Host "Starting vm-mcp server..." -ForegroundColor Cyan
if ($nssmPath) {
    Start-Service -Name $SERVICE_NAME -ErrorAction SilentlyContinue
    Start-Sleep 2
    $status = (Get-Service -Name $SERVICE_NAME -ErrorAction SilentlyContinue).Status
    Write-Host "  Service status: $status" -ForegroundColor $(if ($status -eq "Running") {"Green"} else {"Yellow"})
} else {
    Start-ScheduledTask -TaskName $SERVICE_NAME -ErrorAction SilentlyContinue
    Start-Sleep 2
    Write-Host "  Scheduled task started." -ForegroundColor Green
}

# ─── Summary ─────────────────────────────────────────────────────────────────
Write-Host ""
Write-Host "=====================================================" -ForegroundColor Cyan
Write-Host "  Installation complete!" -ForegroundColor Green
Write-Host "=====================================================" -ForegroundColor Cyan

# Get public IP
try {
    $publicIp = (Invoke-WebRequest -Uri "https://api.ipify.org" -UseBasicParsing -TimeoutSec 5).Content.Trim()
} catch {
    $publicIp = "<your-vm-public-ip>"
}

Write-Host ""
Write-Host "  Server URL  : http://${publicIp}:${PORT}/sse" -ForegroundColor White
Write-Host "  API Key     : $API_KEY" -ForegroundColor Yellow
Write-Host "  Log file    : $INSTALL_DIR\vm-mcp.log" -ForegroundColor White
Write-Host ""
Write-Host "  Add to Kiro CLI mcp.json:" -ForegroundColor Cyan
Write-Host @"
  {
    "mcpServers": {
      "vm-mcp": {
        "url": "http://${publicIp}:${PORT}/sse",
        "headers": {
          "X-Api-Key": "$API_KEY"
        }
      }
    }
  }
"@ -ForegroundColor White
Write-Host ""
Write-Host "  PORT FORWARDING: If the VM is behind NAT, you must" -ForegroundColor Yellow
Write-Host "  forward TCP port $PORT on your router to this VM's" -ForegroundColor Yellow
Write-Host "  local IP, OR use the included tunnel.ps1 (ngrok)." -ForegroundColor Yellow
Write-Host ""
