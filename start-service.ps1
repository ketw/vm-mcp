# start-service.ps1 -- Start the vm-mcp server (run after install.ps1)
# Run this if the service shows Stopped after install.

#Requires -RunAsAdministrator
$ErrorActionPreference = "SilentlyContinue"

$SERVICE_NAME = "VmMcpServer"
$TASK_NAME    = "VmMcpTask"
$INSTALL_DIR  = "C:\VmMcp"

Write-Host "vm-mcp service startup fix" -ForegroundColor Cyan
Write-Host ""

# Resolve python exe and env vars
$pythonExe = (Get-Command python -ErrorAction SilentlyContinue).Source
$apiKey    = [System.Environment]::GetEnvironmentVariable("VM_MCP_API_KEY", "Machine")
$port      = [System.Environment]::GetEnvironmentVariable("VM_MCP_PORT",    "Machine")
if (-not $port) { $port = "8765" }

# 1. Refresh PATH in this session
$machinePath = [System.Environment]::GetEnvironmentVariable("PATH", "Machine")
$userPath    = [System.Environment]::GetEnvironmentVariable("PATH", "User")
$env:PATH    = $machinePath + ";" + $userPath

# ---------------------------------------------------------------------------
# Try NSSM service first
# ---------------------------------------------------------------------------
$nssmCmd = Get-Command nssm -ErrorAction SilentlyContinue
if ($nssmCmd -and $pythonExe) {
    Write-Host "  Reconfiguring NSSM service with full Python path..." -ForegroundColor Gray

    $pythonDir = Split-Path $pythonExe -Parent
    # Single AppEnvironmentExtra call with all vars + PATH
    $envBlock = "VM_MCP_API_KEY=$apiKey`nVM_MCP_PORT=$port`nVM_MCP_HOST=0.0.0.0`nPATH=$pythonDir;$pythonDir\Scripts;$env:SystemRoot\System32;$env:SystemRoot"
    & nssm set $SERVICE_NAME Application      $pythonExe                   2>$null
    & nssm set $SERVICE_NAME AppParameters    "`"$INSTALL_DIR\server.py`"" 2>$null
    & nssm set $SERVICE_NAME AppDirectory     $INSTALL_DIR                 2>$null
    & nssm set $SERVICE_NAME AppEnvironmentExtra $envBlock                 2>$null
    & nssm set $SERVICE_NAME ObjectName       LocalSystem                  2>$null
}

Write-Host "  Starting NSSM service '$SERVICE_NAME'..." -ForegroundColor Yellow
Start-Service -Name $SERVICE_NAME -ErrorAction SilentlyContinue
Start-Sleep -Seconds 4
$status = (Get-Service -Name $SERVICE_NAME -ErrorAction SilentlyContinue).Status

if ($status -eq "Running") {
    Write-Host "  NSSM service is Running." -ForegroundColor Green
} else {
    Write-Host "  NSSM service status: $status -- switching to Task Scheduler..." -ForegroundColor Yellow
    Write-Host ""

    # -----------------------------------------------------------------------
    # Fallback: Scheduled Task (runs the bat file which has full python path)
    # -----------------------------------------------------------------------

    # Ensure start.bat exists with full python path
    if ($pythonExe) {
        $batContent = "@echo off`r`nset VM_MCP_API_KEY=$apiKey`r`nset VM_MCP_PORT=$port`r`nset VM_MCP_HOST=0.0.0.0`r`n`"$pythonExe`" `"$INSTALL_DIR\server.py`""
        [System.IO.File]::WriteAllText("$INSTALL_DIR\start.bat", $batContent, [System.Text.Encoding]::ASCII)
        Write-Host "  Wrote start.bat with full Python path: $pythonExe" -ForegroundColor Gray
    }

    # Remove old task if present
    Unregister-ScheduledTask -TaskName $TASK_NAME -Confirm:$false -ErrorAction SilentlyContinue

    $action    = New-ScheduledTaskAction `
                    -Execute   "$env:SystemRoot\System32\cmd.exe" `
                    -Argument  "/c `"$INSTALL_DIR\start.bat`" > `"$INSTALL_DIR\vm-mcp.log`" 2>&1"
    $trigger   = New-ScheduledTaskTrigger -AtStartup
    $principal = New-ScheduledTaskPrincipal -UserId "SYSTEM" -LogonType ServiceAccount -RunLevel Highest
    $settings  = New-ScheduledTaskSettingsSet `
                    -AllowStartIfOnBatteries `
                    -DontStopIfGoingOnBatteries `
                    -StartWhenAvailable `
                    -ExecutionTimeLimit (New-TimeSpan -Days 365)

    Register-ScheduledTask `
        -TaskName  $TASK_NAME `
        -Action    $action `
        -Trigger   $trigger `
        -Principal $principal `
        -Settings  $settings `
        -Force | Out-Null

    Write-Host "  Scheduled Task '$TASK_NAME' registered." -ForegroundColor Green
    Write-Host "  Starting task now..." -ForegroundColor Yellow
    Start-ScheduledTask -TaskName $TASK_NAME -ErrorAction SilentlyContinue
    Start-Sleep -Seconds 4
}

# ---------------------------------------------------------------------------
# Health check
# ---------------------------------------------------------------------------
Write-Host ""
Write-Host "  Health check on port $port..." -ForegroundColor Gray
Start-Sleep -Seconds 2
try {
    $resp = Invoke-WebRequest -Uri "http://localhost:$port/sse" -TimeoutSec 5 -UseBasicParsing -Headers @{"X-Api-Key"=$apiKey}
    Write-Host "  Server responded -- OK" -ForegroundColor Green
} catch {
    # A connection reset or partial read on SSE is fine -- it means the server is up
    if ($_.Exception.Message -match "forcibly closed|connection|partial") {
        Write-Host "  Server is up (SSE endpoint responded)." -ForegroundColor Green
    } else {
        Write-Host "  No response on port $port : $($_.Exception.Message)" -ForegroundColor Red
        Write-Host ""
        Write-Host "  Error log:" -ForegroundColor Yellow
        if (Test-Path "$INSTALL_DIR\vm-mcp-err.log") { Get-Content "$INSTALL_DIR\vm-mcp-err.log" -Tail 30 }
        if (Test-Path "$INSTALL_DIR\vm-mcp.log")     { Get-Content "$INSTALL_DIR\vm-mcp.log"     -Tail 20 }
        Write-Host ""
        Write-Host "  Run manually to see the error:" -ForegroundColor Yellow
        Write-Host "    `$env:VM_MCP_API_KEY = '$apiKey'" -ForegroundColor White
        Write-Host "    `$env:VM_MCP_PORT    = '$port'"   -ForegroundColor White
        Write-Host "    python $INSTALL_DIR\server.py"    -ForegroundColor White
        exit 1
    }
}

Write-Host ""
Write-Host "  Server URL : http://<your-public-ip>:$port/sse" -ForegroundColor Cyan
Write-Host "  API Key    : $apiKey" -ForegroundColor Cyan
Write-Host ""
