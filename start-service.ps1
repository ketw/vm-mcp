# start-service.ps1 -- Start the vm-mcp server (run after install.ps1)
# Run this if the service shows Stopped after install.
#
# The service may be Stopped because:
#   1. NSSM was installed in the same session and Python path wasn't resolved yet
#   2. The service needs pywin32 post-install hook to run first
#
# This script fixes both and starts the server.

#Requires -RunAsAdministrator
$ErrorActionPreference = "SilentlyContinue"

$SERVICE_NAME = "VmMcpServer"
$INSTALL_DIR  = "C:\VmMcp"

Write-Host "vm-mcp service startup fix" -ForegroundColor Cyan
Write-Host ""

# 1. Run pywin32 post-install (registers COM objects, needed on first install)
Write-Host "  Running pywin32 post-install..." -ForegroundColor Gray
$pythonExe = (Get-Command python -ErrorAction SilentlyContinue).Source
if ($pythonExe) {
    & python "$((Get-Item $pythonExe).DirectoryName)\Lib\site-packages\win32\scripts\pywin32_postinstall.py" -install 2>$null
}

# 2. Refresh PATH in this session
$machinePath = [System.Environment]::GetEnvironmentVariable("PATH", "Machine")
$userPath    = [System.Environment]::GetEnvironmentVariable("PATH", "User")
$env:PATH    = $machinePath + ";" + $userPath

# 3. Set the env vars the service needs (in case they were set after NSSM registered)
$apiKey = [System.Environment]::GetEnvironmentVariable("VM_MCP_API_KEY", "Machine")
$port   = [System.Environment]::GetEnvironmentVariable("VM_MCP_PORT",    "Machine")
if (-not $port) { $port = "8765" }

# Re-apply environment to the NSSM service registration (single call — multiple calls overwrite each other)
$nssmCmd = Get-Command nssm -ErrorAction SilentlyContinue
if ($nssmCmd) {
    $pythonDir = if ($pythonExe) { Split-Path $pythonExe -Parent } else { "" }
    $envBlock  = "VM_MCP_API_KEY=$apiKey`nVM_MCP_PORT=$port`nVM_MCP_HOST=0.0.0.0`nPATH=$pythonDir;$pythonDir\Scripts;$env:SystemRoot\System32;$env:SystemRoot"
    & nssm set $SERVICE_NAME AppEnvironmentExtra $envBlock 2>$null

    # Also make sure Application points to the full Python path
    if ($pythonExe) {
        & nssm set $SERVICE_NAME Application $pythonExe 2>$null
        & nssm set $SERVICE_NAME AppParameters "`"$INSTALL_DIR\server.py`"" 2>$null
    }
}

# 4. Start the service
Write-Host "  Starting service '$SERVICE_NAME'..." -ForegroundColor Yellow
Start-Service -Name $SERVICE_NAME -ErrorAction SilentlyContinue
Start-Sleep -Seconds 3

$status = (Get-Service -Name $SERVICE_NAME -ErrorAction SilentlyContinue).Status

if ($status -eq "Running") {
    Write-Host "  Service is Running." -ForegroundColor Green
    Write-Host ""

    # Quick health check
    try {
        $resp = Invoke-WebRequest -Uri "http://localhost:$port/health" -TimeoutSec 3 -UseBasicParsing
        Write-Host "  Health check: $($resp.Content)" -ForegroundColor Green
    } catch {
        Write-Host "  Health check on port $port failed -- check log:" -ForegroundColor Yellow
        Write-Host "    Get-Content $INSTALL_DIR\vm-mcp-err.log -Tail 20" -ForegroundColor White
    }
} else {
    Write-Host "  Service status: $status" -ForegroundColor Red
    Write-Host ""
    Write-Host "  Checking error log:" -ForegroundColor Yellow
    if (Test-Path "$INSTALL_DIR\vm-mcp-err.log") {
        Get-Content "$INSTALL_DIR\vm-mcp-err.log" -Tail 30
    } else {
        Write-Host "  No error log found at $INSTALL_DIR\vm-mcp-err.log" -ForegroundColor Gray
    }
    Write-Host ""
    Write-Host "  Try running manually to see the error:" -ForegroundColor Yellow
    Write-Host "    `$env:VM_MCP_API_KEY = '$apiKey'" -ForegroundColor White
    Write-Host "    `$env:VM_MCP_PORT    = '$port'" -ForegroundColor White
    Write-Host "    python $INSTALL_DIR\server.py" -ForegroundColor White
}
