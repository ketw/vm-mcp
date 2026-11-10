# =============================================================================
# uninstall.ps1  --  Remove vm-mcp server and all its components
#
# Run from an elevated PowerShell session on the VM:
#   Set-ExecutionPolicy Bypass -Scope Process
#   .\uninstall.ps1
# =============================================================================

#Requires -RunAsAdministrator

$ErrorActionPreference = "SilentlyContinue"

$SERVICE_NAME = "VmMcpServer"
$INSTALL_DIR  = "C:\VmMcp"
$PORT         = 8765

Write-Host ""
Write-Host "Removing vm-mcp server..." -ForegroundColor Yellow
Write-Host ""

# ---------------------------------------------------------------------------
# Stop and remove Windows service (NSSM path)
# ---------------------------------------------------------------------------
$svc = Get-Service -Name $SERVICE_NAME -ErrorAction SilentlyContinue
if ($svc) {
    Write-Host "  Stopping service '$SERVICE_NAME'..."
    Stop-Service -Name $SERVICE_NAME -Force -ErrorAction SilentlyContinue
    Start-Sleep -Seconds 2

    $nssmCmd = Get-Command nssm -ErrorAction SilentlyContinue
    if ($nssmCmd) {
        & nssm remove $SERVICE_NAME confirm
    } else {
        sc.exe delete $SERVICE_NAME | Out-Null
    }
    Write-Host "  Service removed." -ForegroundColor Green
} else {
    Write-Host "  Service '$SERVICE_NAME' not found (skipping)." -ForegroundColor Gray
}

# ---------------------------------------------------------------------------
# Remove scheduled task (fallback path)
# ---------------------------------------------------------------------------
$task = Get-ScheduledTask -TaskName $SERVICE_NAME -ErrorAction SilentlyContinue
if ($task) {
    Stop-ScheduledTask  -TaskName $SERVICE_NAME -ErrorAction SilentlyContinue
    Unregister-ScheduledTask -TaskName $SERVICE_NAME -Confirm:$false
    Write-Host "  Scheduled task removed." -ForegroundColor Green
} else {
    Write-Host "  Scheduled task '$SERVICE_NAME' not found (skipping)." -ForegroundColor Gray
}

# ---------------------------------------------------------------------------
# Remove firewall rule
# ---------------------------------------------------------------------------
$rule = Get-NetFirewallRule -DisplayName "VmMcpServer" -ErrorAction SilentlyContinue
if ($rule) {
    Remove-NetFirewallRule -DisplayName "VmMcpServer"
    Write-Host "  Firewall rule removed." -ForegroundColor Green
} else {
    Write-Host "  Firewall rule not found (skipping)." -ForegroundColor Gray
}

# ---------------------------------------------------------------------------
# Clear environment variables
# ---------------------------------------------------------------------------
[System.Environment]::SetEnvironmentVariable("VM_MCP_API_KEY", $null, "Machine")
[System.Environment]::SetEnvironmentVariable("VM_MCP_PORT",    $null, "Machine")
[System.Environment]::SetEnvironmentVariable("VM_MCP_HOST",    $null, "Machine")
Write-Host "  Environment variables cleared." -ForegroundColor Green

# ---------------------------------------------------------------------------
# Remove install directory
# ---------------------------------------------------------------------------
if (Test-Path $INSTALL_DIR) {
    Remove-Item -Path $INSTALL_DIR -Recurse -Force
    Write-Host "  Install directory removed: $INSTALL_DIR" -ForegroundColor Green
} else {
    Write-Host "  Install directory not found (skipping)." -ForegroundColor Gray
}

Write-Host ""
Write-Host "vm-mcp uninstalled successfully." -ForegroundColor Green
Write-Host ""
