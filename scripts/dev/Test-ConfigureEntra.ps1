# Offline harness: exercises the REAL (non-WhatIf) path of Configure-Entra.ps1 against a fake Graph.
# Verifies no unsupported $filter clauses, no placeholder leakage, and a fully populated .env.
$ErrorActionPreference = "Stop"
$root = Split-Path (Split-Path $PSScriptRoot -Parent) -Parent
Push-Location $root

$envFile = Join-Path $root ".env"
$backup  = Join-Path $root ".env.harness-backup"
Copy-Item $envFile $backup -Force

# Graph properties that are NOT filterable - mirror of the documented constraints
$unfilterable = @{
    "oauth2PermissionGrants" = @("consentType", "scope")
    "servicePrincipals"      = @("servicePrincipalType")
}
$global:calls = @()
$global:bodies = @()

function Connect-MgGraph { param([Parameter(ValueFromRemainingArguments)]$a) }
function Invoke-MgGraphRequest {
    param($Method, $Uri, $Headers, $Body, $ContentType, $OutputType)
    $global:calls += "$Method $Uri"
    if ($Body) { $global:bodies += "$Method $Uri :: $Body" }

    if ($Uri -match '[<>]' -or $Uri -match '%3C') { throw "PLACEHOLDER LEAKED INTO GRAPH: $Uri" }
    if ($Uri -match "not.{0,3}created") { throw "PLACEHOLDER LEAKED INTO GRAPH: $Uri" }
    # Catches variable shadowing of the Graph host (PowerShell var names are case-insensitive,
    # so a local $g shadows a script-scope $G and the base URL silently disappears).
    if ($Uri -notmatch '^https://graph\.microsoft\.com/') { throw "MALFORMED GRAPH URI (host missing or mangled): $Uri" }

    foreach ($res in $unfilterable.Keys) {
        if ($Uri -match [regex]::Escape($res) -and $Uri -match '\$filter=([^&]*)') {
            $f = [uri]::UnescapeDataString($matches[1])
            foreach ($p in $unfilterable[$res]) {
                if ($f -match "\b$p\s+eq\b") { throw "Request_UnsupportedQuery: '$p' is not filterable on $res -> $f" }
            }
        }
    }

    switch -Regex ($Uri) {
        "applications\(appId=" {
            return [pscustomobject]@{
                id = "bp-object-id"; appId = "11111111-1111-4111-8111-111111111111"
                identifierUris = @("api://11111111-1111-4111-8111-111111111111")
                api = [pscustomobject]@{ oauth2PermissionScopes = @(
                    [pscustomobject]@{ value = "access_agent_as_user"; id = "22222222-2222-4222-8222-222222222222"; isEnabled = $true; type = "User" }) }
            }
        }
        "applications\?"           { return [pscustomobject]@{ value = @() } }   # WebClient does not exist yet
        "/applications$"           { return [pscustomobject]@{ id = "web-object-id"; appId = "aaaaaaaa-1111-2222-3333-444444444444" } }
        "addPassword"              { return [pscustomobject]@{ secretText = "FAKE-SECRET-VALUE" } }
        "servicePrincipals\?"      { return [pscustomobject]@{ value = @([pscustomobject]@{ id = "sp-$([guid]::NewGuid().ToString('N').Substring(0,8))" }) } }
        "/servicePrincipals$"      { return [pscustomobject]@{ id = "new-sp-id" } }
        "oauth2PermissionGrants\?" { return [pscustomobject]@{ value = @() } }
        "oauth2PermissionGrants$"  { return [pscustomobject]@{ id = "grant-id" } }
        default                    { return [pscustomobject]@{ value = @() } }
    }
}

$failed = $false
try {
    & (Join-Path $root "scripts\Configure-Entra.ps1")
} catch {
    Write-Host "`nHARNESS FAILURE: $($_.Exception.Message)" -ForegroundColor Red
    $failed = $true
}

Write-Host "`n--- resulting .env (secrets masked) ---" -ForegroundColor Cyan
$want = @("AGENT365_TENANT_ID","AGENT365_BLUEPRINT_ID","AGENT365_AGENT_ID","AGENT365_BLUEPRINT_CLIENT_SECRET",
          "AGENT365_BLUEPRINT_SCOPE","WEB_CLIENT_ID","WEB_CLIENT_SECRET","WEB_REDIRECT_URI",
          "AUTH_ENABLED","ENABLE_A365_OBSERVABILITY_EXPORTER")
$env2 = @{}
foreach ($l in Get-Content $envFile) { if ($l -match '^\s*([A-Za-z0-9_]+)\s*=(.*)$') { $env2[$matches[1]] = $matches[2].Trim() } }
foreach ($k in $want) {
    $v = $env2[$k]
    $shown = if ($k -match "SECRET") { if ($v) { "***set***" } else { "" } } else { $v }
    $ok = [bool]$v -and $v -notmatch '[<>]'
    if (-not $ok) { $failed = $true }
    Write-Host ("  {0} {1} = {2}" -f $(if ($ok) { "[OK]" } else { "[!!]" }), $k, $shown) -ForegroundColor $(if ($ok) { "Green" } else { "Red" })
}

Copy-Item $backup $envFile -Force
Remove-Item $backup -Force

# The OtelWrite grant is what makes telemetry work at all - assert it was actually POSTed
$otelPost = $global:bodies | Where-Object { $_ -match '^POST .*oauth2PermissionGrants ::' -and $_ -match 'Agent365\.Observability\.OtelWrite' }
if (-not $otelPost) {
    Write-Host "`n[!!] The Agent365.Observability.OtelWrite grant was NOT created." -ForegroundColor Red
    Write-Host "     The exporter would get HTTP 403 and Purview/Defender would stay empty." -ForegroundColor Red
    $failed = $true
} else {
    Write-Host "`n[OK] Agent365.Observability.OtelWrite grant would be created (AllPrincipals)." -ForegroundColor Green
}

Write-Host "`n.env restored. Graph calls made: $($global:calls.Count)" -ForegroundColor DarkGray
Pop-Location
if ($failed) { Write-Host "`nHARNESS: FAILED" -ForegroundColor Red; exit 1 }
Write-Host "`nHARNESS: PASSED - the real run should complete end to end." -ForegroundColor Green
