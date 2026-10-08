<#
.SYNOPSIS
  Add another agent identity (instance) to the existing Agent 365 blueprint and prepare it to run on its own port.

.DESCRIPTION
  The a365 CLI creates one agent identity per blueprint. This script adds more, idempotently:
    1. Agent identity    - POST /beta/serviceprincipals/Microsoft.Graph.AgentIdentity with the BLUEPRINT's own
                           client-credentials token (that is how Entra authorizes identity creation), sponsor = you.
    2. Redirect URI      - adds http://localhost:<Port>/auth/callback to the shared web client app (az session).
    3. Registry record   - Graph /beta/copilot/agentRegistrations via scripts\agent_registry.py (browser sign-in).
    4. Overlay env file  - .env.<suffix> with the per-instance values; everything else comes from .env.
  Observability (OtelWrite) and the Purview Graph scopes are inherited from the blueprint (inheritable permissions,
  see Configure-Entra.ps1), so the new identity needs no consent of its own. The script reports what it inherited
  and runs Sync-AgentPermissions.ps1 so the same grants are listed on the identity in the Entra admin center.

  Prerequisites: .env populated by Configure-Entra.ps1 (blueprint secret, WEB_CLIENT_ID), 'az login' as an admin.

.EXAMPLE
  .\scripts\New-AgentInstance.ps1 -Name "Laszlo-AgentRegistryDemo2 Agent" -Port 8001
  .\scripts\run.ps1 -EnvFile .env.agent2
#>
param(
    [Parameter(Mandatory)][string]$Name,
    [int]$Port = 8001,
    [string]$Description = "Weather and geocoding agent (instance 2, same blueprint)",
    [string]$EnvFile,                 # default: .env.agent<Port-7999>
    [string]$SponsorUpn,              # default: the signed-in az user
    [switch]$SkipRegistry,
    [switch]$WhatIf
)
$ErrorActionPreference = "Stop"
$root = Split-Path $PSScriptRoot -Parent
if (-not $EnvFile) { $EnvFile = Join-Path $root ".env.agent$($Port - 7999)" }
elseif (-not [IO.Path]::IsPathRooted($EnvFile)) { $EnvFile = Join-Path $root $EnvFile }

function Step($m) { Write-Host "==> $m" -ForegroundColor Cyan }
function Info($m) { Write-Host "    $m" }

$envVals = @{}
foreach ($l in Get-Content (Join-Path $root ".env")) { if ($l -match '^\s*([A-Za-z0-9_]+)\s*=(.*)$') { $envVals[$matches[1]] = $matches[2].Trim() } }
$tenantId = $envVals.AGENT365_TENANT_ID; $blueprintId = $envVals.AGENT365_BLUEPRINT_ID
$bpSecret = $envVals.AGENT365_BLUEPRINT_CLIENT_SECRET; $webClientId = $envVals.WEB_CLIENT_ID
foreach ($k in "AGENT365_TENANT_ID", "AGENT365_BLUEPRINT_ID", "AGENT365_BLUEPRINT_CLIENT_SECRET", "WEB_CLIENT_ID") {
    if (-not $envVals[$k]) { throw "$k missing in .env - run scripts\Configure-Entra.ps1 first." }
}

# ── Graph as the admin (az session) ─────────────────────────────────────────────
$acct = az account show 2>$null | ConvertFrom-Json
if (-not $acct -or $acct.tenantId -ne $tenantId) { throw "Run: az login --tenant $tenantId --allow-no-subscriptions" }
$azTok = az account get-access-token --resource https://graph.microsoft.com --query accessToken -o tsv
$H = @{ Authorization = "Bearer $azTok"; "OData-Version" = "4.0" }
function G([string]$m, [string]$u, $b) {
    if ($WhatIf -and $m -ne "GET") { Info "[WhatIf] $m $u"; return $null }
    $p = @{ Method = $m; Uri = "https://graph.microsoft.com$u"; Headers = $H }
    if ($b) { $p.Body = ($b | ConvertTo-Json -Depth 10); $p.ContentType = "application/json" }
    Invoke-RestMethod @p
}
$upn = if ($SponsorUpn) { $SponsorUpn } else { $acct.user.name }
$sponsor = G GET "/v1.0/users/$upn`?`$select=id,userPrincipalName"
Info "Tenant $tenantId | blueprint $blueprintId | sponsor $($sponsor.userPrincipalName)"

# ── 1. Agent identity ────────────────────────────────────────────────────────────
Step "Agent identity '$Name'"
$existing = (G GET "/beta/servicePrincipals/microsoft.graph.agentIdentity?`$filter=agentIdentityBlueprintId eq '$blueprintId'&`$select=id,appId,displayName").value
$ident = $existing | Where-Object displayName -eq $Name | Select-Object -First 1
if ($ident) { Info "exists: $($ident.appId)" }
elseif ($WhatIf) { Info "[WhatIf] would create it with the blueprint token" }
else {
    $bpTok = (Invoke-RestMethod -Method POST "https://login.microsoftonline.com/$tenantId/oauth2/v2.0/token" -Body @{
        client_id = $blueprintId; client_secret = $bpSecret; grant_type = "client_credentials"; scope = "https://graph.microsoft.com/.default" }).access_token
    $body = @{
        displayName = $Name; agentIdentityBlueprintId = $blueprintId
        "sponsors@odata.bind" = @("https://graph.microsoft.com/v1.0/users/$($sponsor.id)")
        "owners@odata.bind"   = @("https://graph.microsoft.com/v1.0/users/$($sponsor.id)")
    } | ConvertTo-Json
    $ident = Invoke-RestMethod -Method POST "https://graph.microsoft.com/beta/serviceprincipals/Microsoft.Graph.AgentIdentity" `
        -Headers @{ Authorization = "Bearer $bpTok"; "OData-Version" = "4.0" } -Body $body -ContentType "application/json"
    Info "created: $($ident.appId)"
}
$agentId = $ident.appId

# ── 2. Redirect URI on the shared web client ─────────────────────────────────────
Step "Web client redirect URI for :$Port"
$redirect = "http://localhost:$Port/auth/callback"
$web = G GET "/v1.0/applications(appId='$webClientId')?`$select=id,web"
$uris = @($web.web.redirectUris)
if ($uris -contains $redirect) { Info "already present" }
else { G PATCH "/v1.0/applications/$($web.id)" @{ web = @{ redirectUris = @($uris + $redirect) } } | Out-Null; Info "added $redirect" }

# ── 3. Inherited permissions (report only) ───────────────────────────────────────
Step "Permissions inherited from the blueprint"
try {
    $inh = (G GET "/beta/applications/microsoft.graph.agentIdentityBlueprint/$blueprintId/inheritablePermissions").value
    foreach ($p in $inh) { Info "$($p.resourceAppId): $(($p.inheritableScopes.scopes) -join ', ')" }
    if (-not $inh) { Write-Host "    none - run Configure-Entra.ps1 (inheritable permissions) or OBO will fail with AADSTS65001" -ForegroundColor Yellow }
} catch { Info "could not read inheritable permissions: $($_.Exception.Message)" }
# Inheritance works at token time but the Entra admin center only lists explicit grants, so mirror them.
if ($agentId) {
    Step "Mirroring blueprint grants onto the agent identity (portal visibility)"
    & (Join-Path $PSScriptRoot "Sync-AgentPermissions.ps1") -BlueprintId $blueprintId -AgentIds $agentId -WhatIf:$WhatIf
}

# ── 4. Registry record ───────────────────────────────────────────────────────────
$regId = ""
if (Test-Path $EnvFile) {
    $prev = Select-String -Path $EnvFile -Pattern '^AGENT365_REGISTRATION_ID=(.+)$' | Select-Object -First 1
    if ($prev) { $regId = $prev.Matches[0].Groups[1].Value.Trim() }
}
if (-not $SkipRegistry -and $agentId -and -not $WhatIf) {
    Step "Agent 365 registry record (browser sign-in, AgentRegistration.ReadWrite.All)"
    $cliApp = (Get-Content (Join-Path $root "a365.config.json") -Raw | ConvertFrom-Json).clientAppId
    $py = Join-Path $root ".venv\Scripts\python.exe"
    $out = & $py (Join-Path $PSScriptRoot "agent_registry.py") --tenant-id $tenantId --client-id $cliApp --agent-id $agentId `
        --blueprint-id $blueprintId --owner-oid $sponsor.id --display-name $Name --description $Description --login-hint $upn `
        --registration-id "$regId"
    $reg = $out | Select-Object -Last 1 | ConvertFrom-Json
    if ($reg.error) { Write-Host "    registry failed: $($reg.error) $($reg.description)$($reg.body)" -ForegroundColor Yellow }
    else { $regId = $reg.id; Info "$($reg.status): $regId" }
}

# ── 5. Overlay env file ──────────────────────────────────────────────────────────
Step "Writing $EnvFile"
$shortName = $Name -replace '\s+Agent$', ''
$lines = @(
    "# Instance overlay for '$Name' - loaded before .env (AGENT_ENV_FILE), so these values win.",
    "# Start with:  .\scripts\run.ps1 -EnvFile $(Split-Path $EnvFile -Leaf)",
    "APP_PORT=$Port",
    "WEB_REDIRECT_URI=$redirect",
    "AGENT365_AGENT_ID=$agentId",
    "AGENT365_AGENT_NAME=$shortName",
    "AGENT365_AGENT_DESCRIPTION=$Description",
    "AGENT365_REGISTRATION_ID=$regId",
    "AGENT365OBSERVABILITY__AGENTID=$agentId",
    "AGENT365OBSERVABILITY__AGENTNAME=$shortName",
    "AGENT365OBSERVABILITY__AGENTDESCRIPTION=$Description"
)
if ($WhatIf) { $lines | ForEach-Object { Info "[WhatIf] $_" } } else { Set-Content $EnvFile $lines -Encoding utf8 }

Write-Host ""
Write-Host "Done. Start it:  .\scripts\run.ps1 -EnvFile $(Split-Path $EnvFile -Leaf)   ->  http://localhost:$Port" -ForegroundColor Green
Write-Host "Purview DLP: add the new app to the policy (picks up all .env.agent* files):  .\scripts\New-PurviewDlpDemo.ps1" -ForegroundColor DarkGray
