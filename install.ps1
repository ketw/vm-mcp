# =============================================================================
# install.ps1  --  Install vm-mcp as a persistent Windows service
#
# Run from an elevated PowerShell session on the VM:
#   Set-ExecutionPolicy Bypass -Scope Process
#   .\install.ps1
# =============================================================================

#Requires -RunAsAdministrator

$ErrorActionPreference = "Stop"

$INSTALL_DIR  = "C:\VmMcp"
$SERVICE_NAME = "VmMcpServer"
$PORT         = 8765
$SCRIPT_DIR   = Split-Path -Parent $MyInvocation.MyCommand.Definition

Write-Host ""
Write-Host "=====================================================" -ForegroundColor Cyan
Write-Host "  Nemo VM MCP Server -- Installer" -ForegroundColor Cyan
Write-Host "=====================================================" -ForegroundColor Cyan
Write-Host ""

# ---------------------------------------------------------------------------
# Step 1: Python
# ---------------------------------------------------------------------------
Write-Host "[1/7] Checking Python..." -ForegroundColor Yellow

$pythonCmd = Get-Command python -ErrorAction SilentlyContinue
if (-not $pythonCmd) {
    Write-Host "  Python not found -- installing via winget..." -ForegroundColor Yellow
    winget install --id Python.Python.3.12 --silent --accept-package-agreements --accept-source-agreements

    # Refresh PATH in this session
    $machinePath = [System.Environment]::GetEnvironmentVariable("PATH", "Machine")
    $userPath    = [System.Environment]::GetEnvironmentVariable("PATH", "User")
    $env:PATH    = $machinePath + ";" + $userPath

    $pythonCmd = Get-Command python -ErrorAction SilentlyContinue
    if (-not $pythonCmd) {
        Write-Host "ERROR: Python still not found. Close this window and reopen an elevated prompt." -ForegroundColor Red
        exit 1
    }
}

$pyVer = & python --version 2>&1
Write-Host "  Found: $pyVer" -ForegroundColor Green

# ---------------------------------------------------------------------------
# Step 2: Copy server files
# ---------------------------------------------------------------------------
Write-Host "[2/7] Copying server files to $INSTALL_DIR..." -ForegroundColor Yellow

New-Item -ItemType Directory -Force -Path $INSTALL_DIR | Out-Null
Copy-Item -Path "$SCRIPT_DIR\src\*" -Destination $INSTALL_DIR -Recurse -Force

Write-Host "  Copied." -ForegroundColor Green

# ---------------------------------------------------------------------------
# Step 3: Install Python dependencies
# ---------------------------------------------------------------------------
Write-Host "[3/7] Installing Python dependencies..." -ForegroundColor Yellow

& python -m pip install --upgrade pip --quiet

# pyyaml is a transitive dep of fastmcp and requires C build tools to compile
# from source on Python 3.13+. Install a pre-built binary wheel first so pip
# never attempts to build it. --prefer-binary tells pip to always pick a wheel
# over an sdist for every package, avoiding all source-build failures.
Write-Host "  Pre-installing pyyaml binary wheel..." -ForegroundColor Gray
& python -m pip install "pyyaml>=6.0" --prefer-binary --quiet

& python -m pip install -r "$INSTALL_DIR\requirements.txt" --prefer-binary
if ($LASTEXITCODE -ne 0) {
    Write-Host ""
    Write-Host "ERROR: pip install failed." -ForegroundColor Red
    Write-Host "  If you see a C++ build error, make sure no package is trying" -ForegroundColor Yellow
    Write-Host "  to compile from source. The --prefer-binary flag should prevent" -ForegroundColor Yellow
    Write-Host "  this, but some packages may not have wheels for Python $pyVer." -ForegroundColor Yellow
    Write-Host "  Try: python -m pip install -r C:\VmMcp\requirements.txt --prefer-binary" -ForegroundColor Yellow
    exit 1
}

Write-Host "  Dependencies installed." -ForegroundColor Green

# ---------------------------------------------------------------------------
# Step 4: API key
# ---------------------------------------------------------------------------
Write-Host "[4/7] Setting up API key..." -ForegroundColor Yellow

$API_KEY      = ""
$existingKey  = [System.Environment]::GetEnvironmentVariable("VM_MCP_API_KEY", "Machine")

if ($existingKey) {
    Write-Host "  Existing API key found in system environment." -ForegroundColor Cyan
    $keepExisting = Read-Host "  Keep existing key? [Y/n]"
    if ($keepExisting -eq "n" -or $keepExisting -eq "N") {
        $existingKey = ""
    }
}

if ($existingKey) {
    $API_KEY = $existingKey
} else {
    $customKey = Read-Host "  Enter API key (press Enter to auto-generate)"
    if ($customKey -ne "") {
        $API_KEY = $customKey
    } else {
        $g1      = [System.Guid]::NewGuid().ToString("N")
        $g2      = [System.Guid]::NewGuid().ToString("N")
        $API_KEY = ($g1 + $g2).ToUpper()
        Write-Host "  Generated API key: $API_KEY" -ForegroundColor Green
        Write-Host "  SAVE THIS KEY -- you need it to connect from Kiro CLI." -ForegroundColor Yellow
    }
}

[System.Environment]::SetEnvironmentVariable("VM_MCP_API_KEY", $API_KEY,     "Machine")
[System.Environment]::SetEnvironmentVariable("VM_MCP_PORT",    "$PORT",       "Machine")
[System.Environment]::SetEnvironmentVariable("VM_MCP_HOST",    "0.0.0.0",    "Machine")

Write-Host "  API key saved to system environment." -ForegroundColor Green

# ---------------------------------------------------------------------------
# Step 5: Write startup batch file
# ---------------------------------------------------------------------------
Write-Host "[5/7] Creating service startup script..." -ForegroundColor Yellow

$batLines = @(
    "@echo off",
    ":: vm-mcp service startup wrapper",
    "set VM_MCP_API_KEY=$API_KEY",
    "set VM_MCP_PORT=$PORT",
    "set VM_MCP_HOST=0.0.0.0",
    "python `"$INSTALL_DIR\server.py`" >> `"$INSTALL_DIR\vm-mcp.log`" 2>&1"
)
$batLines -join "`r`n" | Out-File -FilePath "$INSTALL_DIR\start.bat" -Encoding ASCII

Write-Host "  Startup script: $INSTALL_DIR\start.bat" -ForegroundColor Green

# ---------------------------------------------------------------------------
# Step 6: Register as Windows service (NSSM preferred, schtask fallback)
# ---------------------------------------------------------------------------
Write-Host "[6/7] Registering Windows service '$SERVICE_NAME'..." -ForegroundColor Yellow

$nssmPath = Get-Command nssm -ErrorAction SilentlyContinue
if (-not $nssmPath) {
    Write-Host "  NSSM not found -- attempting to install via winget..." -ForegroundColor Yellow
    winget install --id NSSM.NSSM --silent --accept-package-agreements --accept-source-agreements 2>$null
    $machinePath = [System.Environment]::GetEnvironmentVariable("PATH", "Machine")
    $userPath    = [System.Environment]::GetEnvironmentVariable("PATH", "User")
    $env:PATH    = $machinePath + ";" + $userPath
    $nssmPath    = Get-Command nssm -ErrorAction SilentlyContinue
}

# Remove existing service / task if present
$existingSvc = Get-Service -Name $SERVICE_NAME -ErrorAction SilentlyContinue
if ($existingSvc) {
    Write-Host "  Service already exists -- stopping and removing..." -ForegroundColor Yellow
    Stop-Service -Name $SERVICE_NAME -Force -ErrorAction SilentlyContinue
    Start-Sleep -Seconds 2
    if ($nssmPath) {
        & nssm remove $SERVICE_NAME confirm
    } else {
        sc.exe delete $SERVICE_NAME | Out-Null
    }
    Start-Sleep -Seconds 2
}

$existingTask = Get-ScheduledTask -TaskName $SERVICE_NAME -ErrorAction SilentlyContinue
if ($existingTask) {
    Unregister-ScheduledTask -TaskName $SERVICE_NAME -Confirm:$false
}

$pythonExe = (Get-Command python).Source

if ($nssmPath) {
    Write-Host "  Using NSSM to register service..." -ForegroundColor Cyan

    & nssm install $SERVICE_NAME $pythonExe "$INSTALL_DIR\server.py"
    & nssm set $SERVICE_NAME AppDirectory $INSTALL_DIR

    # Set environment variables one at a time (no line continuation needed)
    & nssm set $SERVICE_NAME AppEnvironmentExtra "VM_MCP_API_KEY=$API_KEY"
    & nssm set $SERVICE_NAME AppEnvironmentExtra "VM_MCP_PORT=$PORT"
    & nssm set $SERVICE_NAME AppEnvironmentExtra "VM_MCP_HOST=0.0.0.0"

    & nssm set $SERVICE_NAME AppStdout "$INSTALL_DIR\vm-mcp.log"
    & nssm set $SERVICE_NAME AppStderr "$INSTALL_DIR\vm-mcp-err.log"
    & nssm set $SERVICE_NAME AppRotateFiles 1
    & nssm set $SERVICE_NAME AppRotateBytes 10485760
    & nssm set $SERVICE_NAME Start SERVICE_AUTO_START
    & nssm set $SERVICE_NAME Description "Nemo VM MCP remote control server"

    Write-Host "  NSSM service registered." -ForegroundColor Green

} else {
    Write-Host "  NSSM not available -- using Scheduled Task fallback..." -ForegroundColor Yellow

    # Write a VBScript launcher (hides the console window)
    $vbsContent = "Set WshShell = CreateObject(""WScript.Shell"")"
    $vbsContent += [System.Environment]::NewLine
    $vbsContent += "WshShell.Run ""cmd /c set VM_MCP_API_KEY=$API_KEY && set VM_MCP_PORT=$PORT && python $INSTALL_DIR\server.py >> $INSTALL_DIR\vm-mcp.log 2>&1"", 0, False"
    $vbsContent | Out-File -FilePath "$INSTALL_DIR\start.vbs" -Encoding ASCII

    $action    = New-ScheduledTaskAction -Execute "wscript.exe" -Argument "`"$INSTALL_DIR\start.vbs`""
    $trigger   = New-ScheduledTaskTrigger -AtStartup
    $principal = New-ScheduledTaskPrincipal -UserId "SYSTEM" -LogonType ServiceAccount -RunLevel Highest
    $settings  = New-ScheduledTaskSettingsSet `
                    -AllowStartIfOnBatteries `
                    -DontStopIfGoingOnBatteries `
                    -StartWhenAvailable `
                    -RestartCount 5

    Register-ScheduledTask `
        -TaskName  $SERVICE_NAME `
        -Action    $action `
        -Trigger   $trigger `
        -Principal $principal `
        -Settings  $settings `
        -Force | Out-Null

    Write-Host "  Scheduled Task '$SERVICE_NAME' registered (runs at startup as SYSTEM)." -ForegroundColor Green
}

# ---------------------------------------------------------------------------
# Step 7: Firewall rule
# ---------------------------------------------------------------------------
Write-Host "[7/7] Opening firewall port $PORT..." -ForegroundColor Yellow

$existingRule = Get-NetFirewallRule -DisplayName "VmMcpServer" -ErrorAction SilentlyContinue
if ($existingRule) {
    Remove-NetFirewallRule -DisplayName "VmMcpServer"
}

New-NetFirewallRule `
    -DisplayName "VmMcpServer" `
    -Direction   Inbound `
    -Protocol    TCP `
    -LocalPort   $PORT `
    -Action      Allow `
    -Profile     Any | Out-Null

Write-Host "  Firewall rule created for port $PORT." -ForegroundColor Green

# ---------------------------------------------------------------------------
# Start the service / task now
# ---------------------------------------------------------------------------
Write-Host ""
Write-Host "Starting vm-mcp server..." -ForegroundColor Cyan

if ($nssmPath) {
    Start-Service -Name $SERVICE_NAME -ErrorAction SilentlyContinue
    Start-Sleep -Seconds 3
    $svcStatus = (Get-Service -Name $SERVICE_NAME -ErrorAction SilentlyContinue).Status
    if ($svcStatus -eq "Running") {
        Write-Host "  Service status: $svcStatus" -ForegroundColor Green
    } else {
        Write-Host "  Service status: $svcStatus (check $INSTALL_DIR\vm-mcp-err.log if not Running)" -ForegroundColor Yellow
    }
} else {
    Start-ScheduledTask -TaskName $SERVICE_NAME -ErrorAction SilentlyContinue
    Start-Sleep -Seconds 3
    Write-Host "  Scheduled task started." -ForegroundColor Green
}

# ---------------------------------------------------------------------------
# Summary
# ---------------------------------------------------------------------------
try {
    $publicIp = (Invoke-WebRequest -Uri "https://api.ipify.org" -UseBasicParsing -TimeoutSec 5).Content.Trim()
} catch {
    $publicIp = "<your-vm-public-ip>"
}

$mcpUrl = "http://${publicIp}:${PORT}/sse"

Write-Host ""
Write-Host "=====================================================" -ForegroundColor Cyan
Write-Host "  Installation complete!" -ForegroundColor Green
Write-Host "=====================================================" -ForegroundColor Cyan
Write-Host ""
Write-Host "  Server URL : $mcpUrl" -ForegroundColor White
Write-Host "  API Key    : $API_KEY" -ForegroundColor Yellow
Write-Host "  Log file   : $INSTALL_DIR\vm-mcp.log" -ForegroundColor White
Write-Host ""
Write-Host "  Add this to Kiro CLI  ~/.kiro/settings/mcp.json :" -ForegroundColor Cyan
Write-Host ""

$jsonSnippet = @"
  {
    "mcpServers": {
      "vm-mcp": {
        "url": "$mcpUrl",
        "headers": {
          "X-Api-Key": "$API_KEY"
        }
      }
    }
  }
"@
Write-Host $jsonSnippet -ForegroundColor White

Write-Host ""
Write-Host "  NOTE: If the VM is behind NAT (VMware default) the public IP" -ForegroundColor Yellow
Write-Host "  above is wrong. Run  .\tunnel.ps1  to get a real public URL" -ForegroundColor Yellow
Write-Host "  via ngrok, or forward TCP port $PORT on your router." -ForegroundColor Yellow
Write-Host ""
