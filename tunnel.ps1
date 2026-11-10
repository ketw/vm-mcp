# =============================================================================
# tunnel.ps1  --  Start a public ngrok tunnel to the vm-mcp server
#
# Use this when the VM is behind NAT (VMware Workstation, home router, etc.)
# and you cannot forward a port directly.
#
# Usage:
#   .\tunnel.ps1 [-AuthToken <ngrok-token>] [-Port 8765]
#
# Free ngrok token: https://dashboard.ngrok.com/get-started/your-authtoken
# =============================================================================

param(
    [string]$AuthToken = "",
    [int]   $Port      = 0
)

$ErrorActionPreference = "Stop"

# Default port: read from env or fall back to 8765
if ($Port -eq 0) {
    $envPort = [System.Environment]::GetEnvironmentVariable("VM_MCP_PORT", "Machine")
    if ($envPort) { $Port = [int]$envPort } else { $Port = 8765 }
}

# Default token: read from env
if ($AuthToken -eq "") {
    $envToken = $env:NGROK_AUTHTOKEN
    if ($envToken) { $AuthToken = $envToken }
}

Write-Host ""
Write-Host "vm-mcp ngrok tunnel launcher" -ForegroundColor Cyan
Write-Host ""

# ---------------------------------------------------------------------------
# Ensure ngrok is installed
# ---------------------------------------------------------------------------
$ngrokCmd = Get-Command ngrok -ErrorAction SilentlyContinue
if (-not $ngrokCmd) {
    Write-Host "ngrok not found -- installing via winget..." -ForegroundColor Yellow
    winget install --id Ngrok.Ngrok --silent --accept-package-agreements --accept-source-agreements

    $machinePath = [System.Environment]::GetEnvironmentVariable("PATH", "Machine")
    $userPath    = [System.Environment]::GetEnvironmentVariable("PATH", "User")
    $env:PATH    = $machinePath + ";" + $userPath

    $ngrokCmd = Get-Command ngrok -ErrorAction SilentlyContinue
    if (-not $ngrokCmd) {
        Write-Host "ERROR: ngrok install failed." -ForegroundColor Red
        Write-Host "Download manually from https://ngrok.com/download" -ForegroundColor Yellow
        exit 1
    }
}

# ---------------------------------------------------------------------------
# Auth token
# ---------------------------------------------------------------------------
if ($AuthToken -eq "") {
    Write-Host "No ngrok auth token provided." -ForegroundColor Yellow
    Write-Host "Get a free one at: https://dashboard.ngrok.com/get-started/your-authtoken"
    $AuthToken = Read-Host "Enter your ngrok auth token"
}

if ($AuthToken -ne "") {
    & ngrok authtoken $AuthToken
}

# ---------------------------------------------------------------------------
# Check vm-mcp is running
# ---------------------------------------------------------------------------
try {
    $null = Invoke-WebRequest -Uri "http://localhost:$Port/health" -TimeoutSec 3 -UseBasicParsing -ErrorAction Stop
    Write-Host "vm-mcp server is running on port $Port." -ForegroundColor Green
} catch {
    Write-Host "WARNING: vm-mcp does not seem to be running on port $Port." -ForegroundColor Yellow
    Write-Host "  Start it first:  Start-Service VmMcpServer" -ForegroundColor Yellow
    $ans = Read-Host "Continue anyway? [y/N]"
    if ($ans -ne "y" -and $ans -ne "Y") { exit 0 }
}

# ---------------------------------------------------------------------------
# Start ngrok tunnel
# ---------------------------------------------------------------------------
Write-Host ""
Write-Host "Starting ngrok tunnel to localhost:$Port ..." -ForegroundColor Cyan
Write-Host "Press Ctrl+C to stop." -ForegroundColor Yellow
Write-Host ""

$ngrokArgs  = "http $Port --log=stdout --log-format=json"
$ngrokProc  = Start-Process `
    -FilePath    "ngrok" `
    -ArgumentList $ngrokArgs `
    -RedirectStandardOutput "$env:TEMP\ngrok_out.txt" `
    -PassThru `
    -WindowStyle Hidden

# Poll the ngrok local API for the tunnel URL (up to 15 s)
$publicUrl = ""
for ($i = 0; $i -lt 15; $i++) {
    Start-Sleep -Seconds 1
    try {
        $resp      = Invoke-WebRequest -Uri "http://localhost:4040/api/tunnels" -UseBasicParsing -TimeoutSec 2
        $tunnelObj = ($resp.Content | ConvertFrom-Json).tunnels
        $found     = $tunnelObj | Where-Object { $_.proto -eq "https" }
        if ($found) {
            $publicUrl = $found.public_url
            break
        }
    } catch {
        # ngrok not ready yet -- keep waiting
    }
}

if ($publicUrl -ne "") {
    $apiKey = [System.Environment]::GetEnvironmentVariable("VM_MCP_API_KEY", "Machine")
    $sseUrl = "$publicUrl/sse"

    Write-Host "=================================================" -ForegroundColor Green
    Write-Host "  Tunnel is live!" -ForegroundColor Green
    Write-Host "=================================================" -ForegroundColor Green
    Write-Host ""
    Write-Host "  Public SSE URL : $sseUrl" -ForegroundColor White
    Write-Host "  API Key        : $apiKey" -ForegroundColor Yellow
    Write-Host "  Dashboard      : http://localhost:4040" -ForegroundColor Gray
    Write-Host ""
    Write-Host "  Add to Kiro CLI  ~/.kiro/settings/mcp.json :" -ForegroundColor Cyan
    Write-Host ""

    $jsonSnippet = @"
  {
    "mcpServers": {
      "vm-mcp": {
        "url": "$sseUrl",
        "headers": {
          "X-Api-Key": "$apiKey"
        }
      }
    }
  }
"@
    Write-Host $jsonSnippet -ForegroundColor White
    Write-Host ""
    Write-Host "Press Ctrl+C to stop the tunnel..." -ForegroundColor Yellow

    try {
        while ($true) { Start-Sleep -Seconds 5 }
    } finally {
        Stop-Process -Id $ngrokProc.Id -ErrorAction SilentlyContinue
        Write-Host ""
        Write-Host "Tunnel stopped." -ForegroundColor Yellow
    }

} else {
    Write-Host "Could not get tunnel URL from ngrok API after 15 s." -ForegroundColor Red
    Write-Host "Open http://localhost:4040 in a browser to find the URL manually." -ForegroundColor Yellow
    Wait-Process -Id $ngrokProc.Id -ErrorAction SilentlyContinue
}
