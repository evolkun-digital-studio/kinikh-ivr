<#
.SYNOPSIS
  Start the permanent tunnel created by setup-permanent-tunnel.ps1.
  The public URL is always the same - nothing to change in Exotel.
  Auto-reconnects if the connection drops.
#>
. (Join-Path $PSScriptRoot "_common.ps1")
Assert-Cloudflared

$TunnelName = Get-EnvValue "CLOUDFLARE_TUNNEL_NAME"
if (-not $TunnelName) { $TunnelName = "kinikh-ivr" }
$ConfigFile = Join-Path (Join-Path $env:USERPROFILE ".cloudflared") "$TunnelName.yml"
if (-not (Test-Path $ConfigFile)) {
    throw "No tunnel config at $ConfigFile. Run scripts\windows\setup-permanent-tunnel.ps1 -Hostname ivr.yourdomain.com first."
}
$PublicUrl = Get-EnvValue "PUBLIC_BASE_URL"
if ($PublicUrl) { Show-ExotelUrls $PublicUrl }

while ($true) {
    Write-Host "Starting tunnel '$TunnelName' -> $PublicUrl" -ForegroundColor Cyan
    cloudflared tunnel --config $ConfigFile run $TunnelName
    Write-Host "Tunnel stopped (exit $LASTEXITCODE). Restarting in 5 seconds... (Ctrl+C to quit)" -ForegroundColor Yellow
    Start-Sleep -Seconds 5
}
