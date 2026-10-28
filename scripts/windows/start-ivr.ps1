<#
.SYNOPSIS
  Start the Kinikh IVR backend and its tunnel together, each in its own window.
  Uses the permanent Cloudflare tunnel if configured, otherwise an ngrok
  static domain if NGROK_DOMAIN is set in .env.
#>
param([int]$Port = 8000)
. (Join-Path $PSScriptRoot "_common.ps1")

$TunnelName = Get-EnvValue "CLOUDFLARE_TUNNEL_NAME"
$NgrokDomain = Get-EnvValue "NGROK_DOMAIN"
if (-not $TunnelName -and $NgrokDomain) {
    # Must be in .env before the backend starts (settings are read once).
    Set-EnvValue "PUBLIC_BASE_URL" "https://$NgrokDomain"
}

$Python = Join-Path $ProjectRoot "venv\Scripts\python.exe"
if (-not (Test-Path $Python)) { throw "venv not found at $Python - create it and pip install -r requirements.txt" }

# Backend: --proxy-headers so the app sees https from the tunnel.
$backendCmd = "Set-Location '$ProjectRoot'; & '$Python' -m uvicorn app.main:app --host 127.0.0.1 --port $Port --proxy-headers --forwarded-allow-ips '*'"
Start-Process powershell -ArgumentList "-NoExit", "-Command", $backendCmd | Out-Null
Write-Host "Backend starting on http://127.0.0.1:$Port" -ForegroundColor Green

if ($TunnelName) {
    $script = Join-Path $PSScriptRoot "start-tunnel.ps1"
} elseif ($NgrokDomain) {
    $script = Join-Path $PSScriptRoot "start-ngrok.ps1"
} else {
    throw "No permanent tunnel configured. Run setup-permanent-tunnel.ps1 (Cloudflare) or set NGROK_DOMAIN in .env (ngrok)."
}
Start-Process powershell -ArgumentList "-NoExit", "-ExecutionPolicy", "Bypass", "-File", $script | Out-Null
Write-Host "Tunnel starting in a new window." -ForegroundColor Green
