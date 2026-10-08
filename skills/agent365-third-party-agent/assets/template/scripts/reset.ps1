<#
.SYNOPSIS
  Reset the demo between customer runs.

  Default      : stop the agent + Jaeger (Jaeger is in-memory, so traces are cleared) and clear the chat sessions.
  -Tenant      : ALSO remove everything the demo created in Entra / Agent 365
                 (a365 cleanup -> blueprint, agent identity, registry entry; plus the <agent>-WebClient app),
                 and reset .env back to AUTH_ENABLED=false.
#>
[CmdletBinding()]
param([switch]$Tenant)
$ErrorActionPreference = "Stop"
$root = Split-Path $PSScriptRoot -Parent
$envFile = Join-Path $root ".env"

Write-Host "==> Stopping local processes" -ForegroundColor Cyan
foreach ($port in 8000, 16686) {
    Get-NetTCPConnection -State Listen -LocalPort $port -ErrorAction SilentlyContinue |
        Select-Object -ExpandProperty OwningProcess -Unique |
        ForEach-Object { Write-Host "    port $port -> PID $_"; Stop-Process -Id $_ -Force -ErrorAction SilentlyContinue }
}

if (-not $Tenant) { Write-Host "Local reset done. Start again with .\scripts\run.ps1" -ForegroundColor Green; return }

$vals = @{}
if (Test-Path $envFile) { Get-Content $envFile | ForEach-Object { if ($_ -match '^\s*([A-Z0-9_]+)\s*=(.*)$') { $vals[$matches[1]] = $matches[2].Trim() } } }
$agentName = if ($vals["AGENT365_AGENT_NAME"]) { $vals["AGENT365_AGENT_NAME"] } else { "{{AGENT_NAME}}" }

Write-Host "==> a365 cleanup (blueprint, agent identity, registry entry)" -ForegroundColor Cyan
Push-Location $root
try { a365 cleanup --agent-name $agentName } finally { Pop-Location }

if ($vals["WEB_CLIENT_ID"]) {
    Write-Host "==> Deleting web client app $($vals['WEB_CLIENT_ID'])" -ForegroundColor Cyan
    Import-Module Microsoft.Graph.Authentication
    Connect-MgGraph -TenantId $vals["AGENT365_TENANT_ID"] -Scopes "Application.ReadWrite.All" -NoWelcome
    $app = (Invoke-MgGraphRequest GET "https://graph.microsoft.com/v1.0/applications?`$filter=appId eq '$($vals['WEB_CLIENT_ID'])'&`$select=id" -OutputType PSObject).value | Select-Object -First 1
    if ($app) { Invoke-MgGraphRequest DELETE "https://graph.microsoft.com/v1.0/applications/$($app.id)" | Out-Null }
}

Write-Host "==> Resetting .env (keeping LLM + session settings)" -ForegroundColor Cyan
$blank = "AGENT365_TENANT_ID","AGENT365_BLUEPRINT_ID","AGENT365_BLUEPRINT_CLIENT_SECRET","AGENT365_AGENT_ID","WEB_CLIENT_ID","WEB_CLIENT_SECRET"
$lines = Get-Content $envFile | ForEach-Object {
    $l = $_
    foreach ($k in $blank) { if ($l -match "^$k=") { $l = "$k=" } }
    if ($l -match '^AUTH_ENABLED=') { $l = "AUTH_ENABLED=false" }
    if ($l -match '^ENABLE_A365_OBSERVABILITY_EXPORTER=') { $l = "ENABLE_A365_OBSERVABILITY_EXPORTER=false" }
    if ($l -match '^AGENT365_BLUEPRINT_SCOPE=') { $l = "AGENT365_BLUEPRINT_SCOPE=api://<blueprint-app-id>/access_agent_as_user" }
    $l
}
Set-Content $envFile $lines -Encoding utf8
Write-Host "Tenant reset done. Deleted agents stay in the Entra recycle bin for 30 days." -ForegroundColor Green
