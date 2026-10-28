# Shared helpers for the Windows tunnel scripts. Dot-source, don't run.

$ErrorActionPreference = "Stop"
$ProjectRoot = Resolve-Path (Join-Path $PSScriptRoot "..\..")
$EnvFile = Join-Path $ProjectRoot ".env"

function Get-EnvValue([string]$Name) {
    if (-not (Test-Path $EnvFile)) { return $null }
    foreach ($line in Get-Content $EnvFile) {
        if ($line -match "^\s*$Name\s*=\s*(.*)$") {
            return $Matches[1].Trim().Trim('"').Trim("'")
        }
    }
    return $null
}

function Set-EnvValue([string]$Name, [string]$Value) {
    if (-not (Test-Path $EnvFile)) { throw ".env not found at $EnvFile - copy .env.example to .env first." }
    $lines = @(Get-Content $EnvFile)
    $found = $false
    for ($i = 0; $i -lt $lines.Count; $i++) {
        if ($lines[$i] -match "^\s*$Name\s*=") { $lines[$i] = "$Name=$Value"; $found = $true }
    }
    if (-not $found) { $lines += "$Name=$Value" }
    # UTF-8 without BOM (PowerShell 5's -Encoding UTF8 adds a BOM that can
    # corrupt the first key when python-dotenv reads the file).
    [System.IO.File]::WriteAllLines($EnvFile, [string[]]$lines)
}

function Show-ExotelUrls([string]$BaseUrl) {
    $base = $BaseUrl.TrimEnd("/")
    $token = Get-EnvValue "EXOTEL_WEBHOOK_SECRET"
    if (-not $token) { $token = "<EXOTEL_WEBHOOK_SECRET from .env>" }
    $incoming = "$base/api/v1/telephony/incoming?webhook_token=$token"
    $status = "$base/api/v1/telephony/status?webhook_token=$token"
    Write-Host ""
    Write-Host "=================== Exotel configuration ===================" -ForegroundColor Cyan
    Write-Host "Voicebot applet URL (App Bazaar > your flow > Voicebot):" -ForegroundColor Yellow
    Write-Host "  $incoming"
    Write-Host "Status callback URL (optional):" -ForegroundColor Yellow
    Write-Host "  $status"
    Write-Host "Health check (open in a browser):" -ForegroundColor Yellow
    Write-Host "  $base/api/v1/health"
    Write-Host "=============================================================" -ForegroundColor Cyan
    try { Set-Clipboard -Value $incoming; Write-Host "(Voicebot URL copied to clipboard)" -ForegroundColor Green } catch {}
}

function Assert-Cloudflared {
    if (-not (Get-Command cloudflared -ErrorAction SilentlyContinue)) {
        Write-Host "cloudflared is not installed. Installing with winget..." -ForegroundColor Yellow
        winget install --id Cloudflare.cloudflared -e --accept-source-agreements --accept-package-agreements
        Write-Host "Installed. Close this window, open a NEW PowerShell window and run the script again." -ForegroundColor Yellow
        exit 1
    }
}
