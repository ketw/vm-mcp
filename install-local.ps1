# install-local.ps1 -- Install vm-mcp on the current device (no admin required)
#
# Runs as the current user — no NSSM, no SYSTEM account.
# Perfect for local dev or using vm-mcp on your own machine.
#
# Usage:
#   Set-ExecutionPolicy Bypass -Scope Process
#   .\install-local.ps1
#
# Optional flags:
#   .\install-local.ps1 -Port 8765 -ApiKey "my-key" -StartNow
# =============================================================================

param(
    [string]$Port    = "8765",
    [string]$ApiKey   = "",
    [string]$InstallDir = "$env:LOCALAPPDATA\VmMcp",
    [switch]$StartNow,
    [switch]$RegisterStartup
)

$ErrorActionPreference = "Stop"
$SCRIPT_DIR = Split-Path -Parent $MyInvocation.MyCommand.Definition

Write-Host ""
Write-Host "=====================================================" -ForegroundColor Cyan
Write-Host "  vm-mcp Local Installer (user mode)" -ForegroundColor Cyan
Write-Host "=====================================================" -ForegroundColor Cyan
Write-Host ""

# ---------------------------------------------------------------------------
# 1. Python
# ---------------------------------------------------------------------------
Write-Host "[1/6] Checking Python..." -ForegroundColor Yellow
# Skip venv and WindowsApps stub Pythons — find a real install with pip
$pythonExe = $null
$allPythons = Get-Command python -All -ErrorAction SilentlyContinue | Where-Object {
    $_.Source -notlike "*WindowsApps*" -and $_.Source -notlike "*venv*"
} | Select-Object -ExpandProperty Source
foreach ($p in $allPythons) {
    $pipTest = & $p -m pip --version 2>$null
    if ($LASTEXITCODE -eq 0) { $pythonExe = $p; break }
}
if (-not $pythonExe) {
    # Fallback: check common locations
    $candidates = @(
        "$env:LOCALAPPDATA\Python\bin\python.exe",
        "C:\Python313\python.exe", "C:\Python312\python.exe",
        "C:\Python311\python.exe", "C:\Python310\python.exe"
    )
    foreach ($p in $candidates) {
        if (Test-Path $p) { $pythonExe = $p; break }
    }
}
if (-not $pythonExe) {
    Write-Host "  ERROR: No suitable Python with pip found." -ForegroundColor Red
    Write-Host "  Install from python.org or: winget install Python.Python.3.12" -ForegroundColor Yellow
    exit 1
}
Write-Host "  Python: $pythonExe" -ForegroundColor Green

# ---------------------------------------------------------------------------
# 2. Install directory
# ---------------------------------------------------------------------------
Write-Host "[2/6] Setting up install dir: $InstallDir..." -ForegroundColor Yellow
New-Item -ItemType Directory -Path $InstallDir -Force | Out-Null

# Copy server.py and requirements.txt from repo
$srcServer = Join-Path $SCRIPT_DIR "src\server.py"
$srcReqs   = Join-Path $SCRIPT_DIR "src\requirements.txt"
if (-not (Test-Path $srcServer)) { $srcServer = Join-Path $SCRIPT_DIR "server.py" }
if (-not (Test-Path $srcReqs))   { $srcReqs   = Join-Path $SCRIPT_DIR "requirements.txt" }

Copy-Item $srcServer (Join-Path $InstallDir "server.py") -Force
if (Test-Path $srcReqs) { Copy-Item $srcReqs (Join-Path $InstallDir "requirements.txt") -Force }
Write-Host "  Copied server.py" -ForegroundColor Green

# ---------------------------------------------------------------------------
# 3. Dependencies
# ---------------------------------------------------------------------------
Write-Host "[3/6] Installing Python packages..." -ForegroundColor Yellow
$reqsPath = Join-Path $InstallDir "requirements.txt"
if (Test-Path $reqsPath) {
    & $pythonExe -m pip install -r $reqsPath --prefer-binary -q
} else {
    # Minimal deps if no requirements.txt
    & $pythonExe -m pip install fastmcp uvicorn starlette httpx Pillow pyautogui psutil pywin32 -q
}
Write-Host "  Dependencies installed." -ForegroundColor Green

# ---------------------------------------------------------------------------
# 4. API key + env vars
# ---------------------------------------------------------------------------
Write-Host "[4/6] Setting up environment..." -ForegroundColor Yellow

if (-not $ApiKey) {
    $existing = [System.Environment]::GetEnvironmentVariable("VM_MCP_API_KEY", "User")
    if ($existing) {
        $ApiKey = $existing
        Write-Host "  Reusing existing API key." -ForegroundColor Cyan
    } else {
        $ApiKey = [System.Guid]::NewGuid().ToString("N").Substring(0, 16)
        Write-Host "  Generated API key: $ApiKey" -ForegroundColor Green
        Write-Host "  SAVE THIS KEY -- you need it for mcp.json." -ForegroundColor Yellow
    }
}

[System.Environment]::SetEnvironmentVariable("VM_MCP_API_KEY", $ApiKey, "User")
[System.Environment]::SetEnvironmentVariable("VM_MCP_PORT", $Port, "User")
[System.Environment]::SetEnvironmentVariable("VM_MCP_HOST", "127.0.0.1", "User")
$env:VM_MCP_API_KEY = $ApiKey
$env:VM_MCP_PORT = $Port
$env:VM_MCP_HOST = "127.0.0.1"
Write-Host "  API Key: $ApiKey" -ForegroundColor Green
Write-Host "  Port: $Port  (localhost only)" -ForegroundColor Green

# ---------------------------------------------------------------------------
# 5. Start script
# ---------------------------------------------------------------------------
Write-Host "[5/6] Creating start script..." -ForegroundColor Yellow

$startBat = "@echo off`r`nset VM_MCP_API_KEY=$ApiKey`r`nset VM_MCP_PORT=$Port`r`nset VM_MCP_HOST=127.0.0.1`r`n`"$pythonExe`" `"$InstallDir\server.py`""
[System.IO.File]::WriteAllText((Join-Path $InstallDir "start.bat"), $startBat, [System.Text.Encoding]::ASCII)

$startPs1 = @"
# Start vm-mcp locally
`$env:VM_MCP_API_KEY = '$ApiKey'
`$env:VM_MCP_PORT = '$Port'
`$env:VM_MCP_HOST = '127.0.0.1'
& '$pythonExe' '$InstallDir\server.py'
"@
[System.IO.File]::WriteAllText((Join-Path $InstallDir "start.ps1"), $startPs1, [System.Text.Encoding]::UTF8)

Write-Host "  start.bat and start.ps1 created." -ForegroundColor Green

# Optional: register as scheduled task (runs at logon as current user)
if ($RegisterStartup) {
    Write-Host "  Registering startup task..." -ForegroundColor Gray
    Unregister-ScheduledTask -TaskName "VmMcpLocal" -Confirm:$false -ErrorAction SilentlyContinue
    $action    = New-ScheduledTaskAction -Execute $pythonExe -Argument "`"$InstallDir\server.py`""
    $trigger   = New-ScheduledTaskTrigger -AtLogOn -User $env:USERNAME
    $principal = New-ScheduledTaskPrincipal -UserId $env:USERNAME -LogonType Interactive -RunLevel Limited
    $settings  = New-ScheduledTaskSettingsSet -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries -StartWhenAvailable
    Register-ScheduledTask -TaskName "VmMcpLocal" -Action $action -Trigger $trigger -Principal $principal -Settings $settings -Force | Out-Null
    Write-Host "  Startup task registered (runs at logon)." -ForegroundColor Green
}

# ---------------------------------------------------------------------------
# 6. Summary + mcp.json snippet
# ---------------------------------------------------------------------------
Write-Host "[6/6] Done!" -ForegroundColor Yellow
Write-Host ""
Write-Host "=====================================================" -ForegroundColor Cyan
Write-Host "  Installation complete!" -ForegroundColor Green
Write-Host "=====================================================" -ForegroundColor Cyan
Write-Host ""
Write-Host "  Install dir : $InstallDir" -ForegroundColor White
Write-Host "  Server URL : http://localhost:$Port/sse" -ForegroundColor White
Write-Host "  API Key    : $ApiKey" -ForegroundColor White
Write-Host ""
Write-Host "  Start manually:" -ForegroundColor Gray
Write-Host "    & '$InstallDir\start.ps1'" -ForegroundColor White
Write-Host ""
Write-Host "  Or add to Kiro CLI ~/.kiro/settings/mcp.json :" -ForegroundColor Yellow
Write-Host ""
$jsonSnippet = @"
  {
    "mcpServers": {
      "vm-local": {
        "url": "http://localhost:$Port/sse",
        "headers": { "X-Api-Key": "$ApiKey" }
      }
    }
  }
"@
Write-Host $jsonSnippet -ForegroundColor White
Write-Host ""

if ($StartNow) {
    Write-Host "  Starting server now..." -ForegroundColor Yellow
    Start-Process -FilePath $pythonExe -ArgumentList "`"$InstallDir\server.py`"" -WindowStyle Minimized
    Start-Sleep -Seconds 3
    try {
        $resp = Invoke-WebRequest -Uri "http://localhost:$Port/sse" -Headers @{"X-Api-Key"=$ApiKey} -TimeoutSec 3 -UseBasicParsing
        Write-Host "  Server is running on port $Port!" -ForegroundColor Green
    } catch {
        if ($_.Exception.Message -match "connection|forcibly|partial") {
            Write-Host "  Server appears to be running on port $Port." -ForegroundColor Green
        } else {
            Write-Host "  Could not verify server. Check with start.ps1" -ForegroundColor Yellow
        }
    }
} else {
    Write-Host "  To start: run start.ps1 in $InstallDir" -ForegroundColor Yellow
}
Write-Host ""
