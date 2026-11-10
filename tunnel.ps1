# =============================================================================
# tunnel.ps1 — Start a public ngrok tunnel to the vm-mcp server
#
# Use this if the VM is behind NAT (VMware NAT, home router, etc.)
# and you can't forward a port. ngrok gives you a public HTTPS URL.
#
# Usage (run as admin or normal user after install.ps1):
#   .\tunnel.ps1 [-AuthToken <ngrok-token>]
#
# Get a free ngrok token at: https://dashboard.ngrok.com/get-started/your-authtoken
# =============================================================================

param(
    [string]$AuthToken = $env:NGROK_AUTHTOKEN,
    [int]$Port = $env:VM_MCP_PORT ?? 8765
)

$ErrorActionPreference = "Stop"

Write-Host "vm-mcp ngrok tunnel launcher" -ForegroundColor Cyan
Write-Host ""

# ─── Ensure ngrok is installed ───────────────────────────────────────────────
$ngrok = Get-Command ngrok -ErrorAction SilentlyContinue
if (-not $ngrok) {
    Write-Host "ngrok not found — installing via winget..."
    winget install --id Ngrok.Ngrok --silent --accept-package-agreements --accept-source-agreements
    $env:PATH = [System.Environment]::GetEnvironmentVariable("PATH", "Machine") + ";" +
                [System.Environment]::GetEnvironmentVariable("PATH", "User")
    $ngrok = Get-Command ngrok -ErrorAction SilentlyContinue
    if (-not $ngrok) {
        Write-Host "ERROR: ngrok install failed. Download manually from https://ngrok.com/download" -ForegroundColor Red
        exit 1
    }
}

# ─── Set auth token ──────────────────────────────────────────────────────────
if (-not $AuthToken) {
    Write-Host "No ngrok auth token provided."
    Write-Host "Get a free one at: https://dashboard.ngrok.com/get-started/your-authtoken" -ForegroundColor Yellow
    $AuthToken = Read-Host "Enter your ngrok auth token"
}

if ($AuthToken) {
    & ngrok authtoken $AuthToken
}

# ─── Verify vm-mcp is running ────────────────────────────────────────────────
try {
    $resp = Invoke-WebRequest -Uri "http://localhost:$Port/health" -TimeoutSec 3 -UseBasicParsing -ErrorAction Stop
    Write-Host "vm-mcp server is running on port $Port." -ForegroundColor Green
} catch {
    Write-Host "WARNING: vm-mcp server does not appear to be running on port $Port." -ForegroundColor Yellow
    Write-Host "Start it first: Start-Service VmMcpServer" -ForegroundColor Yellow
    $continue = Read-Host "Continue anyway? [y/N]"
    if ($continue -ne "y" -and $continue -ne "Y") { exit 0 }
}

# ─── Start tunnel ────────────────────────────────────────────────────────────
Write-Host ""
Write-Host "Starting ngrok tunnel to localhost:$Port..." -ForegroundColor Cyan
Write-Host "The public URL will appear below. Copy the 'https://' URL." -ForegroundColor Yellow
Write-Host "Press Ctrl+C to stop the tunnel." -ForegroundColor Yellow
Write-Host ""

# Start ngrok and capture the public URL
$ngrokJob = Start-Process -FilePath "ngrok" `
    -ArgumentList "http $Port --log=stdout --log-format=json" `
    -RedirectStandardOutput "$env:TEMP\ngrok_out.txt" `
    -PassThru -WindowStyle Hidden

# Poll the ngrok local API for the public URL
$publicUrl = ""
$maxWait = 15
for ($i = 0; $i -lt $maxWait; $i++) {
    Start-Sleep 1
    try {
        $tunnels = (Invoke-WebRequest -Uri "http://localhost:4040/api/tunnels" -UseBasicParsing -TimeoutSec 2).Content | ConvertFrom-Json
        $publicUrl = $tunnels.tunnels | Where-Object { $_.proto -eq "https" } | Select-Object -ExpandProperty public_url -First 1
        if ($publicUrl) { break }
    } catch {}
}

if ($publicUrl) {
    $apiKey = [System.Environment]::GetEnvironmentVariable("VM_MCP_API_KEY", "Machine")

    Write-Host ""
    Write-Host "=================================================" -ForegroundColor Green
    Write-Host "  Tunnel is live!" -ForegroundColor Green
    Write-Host "=================================================" -ForegroundColor Green
    Write-Host ""
    Write-Host "  Public SSE URL: $publicUrl/sse" -ForegroundColor White
    Write-Host ""
    Write-Host "  Add to Kiro CLI ~/.kiro/settings/mcp.json:" -ForegroundColor Cyan
    Write-Host @"
  {
    "mcpServers": {
      "vm-mcp": {
        "url": "$publicUrl/sse",
        "headers": {
          "X-Api-Key": "$apiKey"
        }
      }
    }
  }
"@ -ForegroundColor White
    Write-Host ""
    Write-Host "  Tunnel dashboard: http://localhost:4040" -ForegroundColor Gray
    Write-Host ""
    Write-Host "Press Ctrl+C to stop..." -ForegroundColor Yellow

    # Keep alive until Ctrl+C
    try {
        while ($true) { Start-Sleep 5 }
    } finally {
        Stop-Process -Id $ngrokJob.Id -ErrorAction SilentlyContinue
        Write-Host "Tunnel stopped." -ForegroundColor Yellow
    }
} else {
    Write-Host "Could not retrieve tunnel URL from ngrok API." -ForegroundColor Red
    Write-Host "Check http://localhost:4040 in a browser for the tunnel URL." -ForegroundColor Yellow
    # Keep process alive so user can check
    Wait-Process -Id $ngrokJob.Id -ErrorAction SilentlyContinue
}
