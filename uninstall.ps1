#Requires -RunAsAdministrator
# uninstall.ps1 — Remove vm-mcp server and all its components

$SERVICE_NAME = "VmMcpServer"
$INSTALL_DIR  = "C:\VmMcp"
$PORT         = 8765

Write-Host "Removing vm-mcp server..." -ForegroundColor Yellow

# Stop and remove service (NSSM)
$nssmPath = Get-Command nssm -ErrorAction SilentlyContinue
$svc = Get-Service -Name $SERVICE_NAME -ErrorAction SilentlyContinue
if ($svc) {
    Stop-Service -Name $SERVICE_NAME -Force -ErrorAction SilentlyContinue
    if ($nssmPath) { & nssm remove $SERVICE_NAME confirm }
    else { sc.exe delete $SERVICE_NAME }
    Write-Host "  Service removed." -ForegroundColor Green
}

# Remove scheduled task (fallback method)
$task = Get-ScheduledTask -TaskName $SERVICE_NAME -ErrorAction SilentlyContinue
if ($task) {
    Stop-ScheduledTask  -TaskName $SERVICE_NAME -ErrorAction SilentlyContinue
    Unregister-ScheduledTask -TaskName $SERVICE_NAME -Confirm:$false
    Write-Host "  Scheduled task removed." -ForegroundColor Green
}

# Remove firewall rule
Remove-NetFirewallRule -DisplayName "VmMcpServer" -ErrorAction SilentlyContinue
Write-Host "  Firewall rule removed." -ForegroundColor Green

# Remove environment variables
[System.Environment]::SetEnvironmentVariable("VM_MCP_API_KEY", $null, "Machine")
[System.Environment]::SetEnvironmentVariable("VM_MCP_PORT",    $null, "Machine")
Write-Host "  Environment variables cleared." -ForegroundColor Green

# Remove install directory
if (Test-Path $INSTALL_DIR) {
    Remove-Item -Path $INSTALL_DIR -Recurse -Force
    Write-Host "  Install directory removed: $INSTALL_DIR" -ForegroundColor Green
}

Write-Host ""
Write-Host "vm-mcp uninstalled successfully." -ForegroundColor Green
