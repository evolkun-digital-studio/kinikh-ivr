<#
.SYNOPSIS
  ONE-TIME setup of a permanent Cloudflare tunnel with a fixed URL
  (e.g. https://ivr.yourdomain.com), so the Exotel Voicebot URL never has to
  change again - not after restarting the tunnel, the app or the PC.

.REQUIREMENTS
  A domain whose DNS is managed by Cloudflare (free plan is fine). Any
  domain you own works - you only need one sub-domain, e.g. ivr.evolkun.com.

.EXAMPLE
  powershell -ExecutionPolicy Bypass -File scripts\windows\setup-permanent-tunnel.ps1 -Hostname ivr.yourdomain.com
#>
param(
    [Parameter(Mandatory = $true)][string]$Hostname,
    [string]$TunnelName = "kinikh-ivr",
    [int]$Port = 8000
)
. (Join-Path $PSScriptRoot "_common.ps1")
Assert-Cloudflared

$CfDir = Join-Path $env:USERPROFILE ".cloudflared"
New-Item -ItemType Directory -Force -Path $CfDir | Out-Null

# 1. Log in to Cloudflare once (opens a browser - pick the domain).
if (-not (Test-Path (Join-Path $CfDir "cert.pem"))) {
    Write-Host "Step 1/4: A browser will open - log in to Cloudflare and select your domain." -ForegroundColor Cyan
    cloudflared tunnel login
    if ($LASTEXITCODE -ne 0) { throw "cloudflared login failed" }
} else {
    Write-Host "Step 1/4: Already logged in to Cloudflare." -ForegroundColor Green
}

# 2. Create the named tunnel (re-uses it if it already exists).
$existing = (cloudflared tunnel list --output json | ConvertFrom-Json) | Where-Object { $_.name -eq $TunnelName }
if ($existing) {
    $TunnelId = $existing.id
    Write-Host "Step 2/4: Tunnel '$TunnelName' already exists ($TunnelId)." -ForegroundColor Green
} else {
    Write-Host "Step 2/4: Creating tunnel '$TunnelName'..." -ForegroundColor Cyan
    cloudflared tunnel create $TunnelName
    if ($LASTEXITCODE -ne 0) { throw "tunnel create failed" }
    $TunnelId = ((cloudflared tunnel list --output json | ConvertFrom-Json) | Where-Object { $_.name -eq $TunnelName }).id
}
$CredFile = Join-Path $CfDir "$TunnelId.json"
if (-not (Test-Path $CredFile)) {
    throw "Credentials file $CredFile not found. Delete the tunnel in the Cloudflare dashboard and run this script again."
}

# 3. Point the fixed hostname at the tunnel (creates the DNS record).
Write-Host "Step 3/4: Routing https://$Hostname to the tunnel..." -ForegroundColor Cyan
cloudflared tunnel route dns --overwrite-dns $TunnelName $Hostname
if ($LASTEXITCODE -ne 0) { throw "DNS route failed - is $Hostname on a domain in your Cloudflare account?" }

# 4. Write the tunnel config + remember the public URL in .env.
$ConfigFile = Join-Path $CfDir "$TunnelName.yml"
@"
tunnel: $TunnelId
credentials-file: $CredFile
# Keep WebSocket media streams alive and fast.
protocol: http2
ingress:
  - hostname: $Hostname
    service: http://localhost:$Port
    originRequest:
      noTLSVerify: true
      connectTimeout: 10s
  - service: http_status:404
"@ | Set-Content -Path $ConfigFile -Encoding ASCII

Set-EnvValue "PUBLIC_BASE_URL" "https://$Hostname"
Set-EnvValue "CLOUDFLARE_TUNNEL_NAME" $TunnelName
Write-Host "Step 4/4: Config written to $ConfigFile and PUBLIC_BASE_URL saved in .env" -ForegroundColor Green

Show-ExotelUrls "https://$Hostname"
Write-Host ""
Write-Host "Paste the Voicebot URL into Exotel ONE last time. From now on start everything with:" -ForegroundColor Cyan
Write-Host "  powershell -ExecutionPolicy Bypass -File scripts\windows\start-ivr.ps1"
