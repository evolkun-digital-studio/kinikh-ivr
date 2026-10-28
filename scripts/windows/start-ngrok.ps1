<#
.SYNOPSIS
  Alternative if you have NO domain on Cloudflare: ngrok gives every free
  account one permanent "static domain" (e.g. kinikh-ivr.ngrok-free.app).

  One-time: sign up at ngrok.com, run `ngrok config add-authtoken <token>`,
  claim your free static domain (Dashboard > Domains), then put it in .env:
      NGROK_DOMAIN=your-name.ngrok-free.app
#>
param([int]$Port = 8000)
. (Join-Path $PSScriptRoot "_common.ps1")
if (-not (Get-Command ngrok -ErrorAction SilentlyContinue)) {
    winget install --id Ngrok.Ngrok -e --accept-source-agreements --accept-package-agreements
    Write-Host "Installed ngrok. Open a NEW PowerShell window and run this again." -ForegroundColor Yellow
    exit 1
}
$Domain = Get-EnvValue "NGROK_DOMAIN"
if (-not $Domain) { throw "Set NGROK_DOMAIN=your-name.ngrok-free.app in .env first." }
Set-EnvValue "PUBLIC_BASE_URL" "https://$Domain"
Show-ExotelUrls "https://$Domain"
while ($true) {
    ngrok http --url=$Domain $Port
    Write-Host "ngrok stopped. Restarting in 5 seconds... (Ctrl+C to quit)" -ForegroundColor Yellow
    Start-Sleep -Seconds 5
}
