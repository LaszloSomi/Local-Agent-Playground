<#
.SYNOPSIS
  Check local configuration prerequisites for Microsoft 365 telemetry.

  Read-only. No sign-in, no Graph calls, no tenant changes. Run it before you go looking
  in Purview or Defender. This does not test credentials, consent, delivery, or content access.

.PARAMETER Tenant  Also run read-only tenant checks through the a365 CLI (uses its cached sign-in):
                   the blueprint's inheritable permissions and the agent identity's OtelWrite grant.
#>
[CmdletBinding()]
param([switch]$Tenant)
$ErrorActionPreference = "Stop"
$root = Split-Path $PSScriptRoot -Parent
$envFile = Join-Path $root ".env"

function Read-DotEnv {
    $h = [ordered]@{}
    if (Test-Path $envFile) {
        foreach ($l in Get-Content $envFile) {
            if ($l -match '^\s*([A-Za-z0-9_]+)\s*=(.*)$') { $h[$matches[1]] = $matches[2].Trim() }
        }
    }
    $h
}
function Line($ok, $label, $detail) {
    $mark = if ($ok) { "[ OK ]" } else { "[FAIL]" }
    $col  = if ($ok) { "Green" } else { "Red" }
    Write-Host $mark -ForegroundColor $col -NoNewline
    Write-Host " $label" -NoNewline
    if ($detail) { Write-Host "  $detail" -ForegroundColor DarkGray } else { Write-Host "" }
}

if (-not (Test-Path $envFile)) { throw ".env not found. Run scripts\setup.ps1 first." }
$e = Read-DotEnv
$isGuid = { param($v) $v -and ($v -as [guid]) }

Write-Host "`n--- Local demo (Jaeger) ---" -ForegroundColor Cyan
$jaeger = $null -ne (Get-NetTCPConnection -LocalPort 16686 -State Listen -ErrorAction SilentlyContinue)
$app    = $null -ne (Get-NetTCPConnection -LocalPort 8000  -State Listen -ErrorAction SilentlyContinue)
Line $jaeger "Jaeger listening on :16686"
Line $app    "Agent app listening on :8000"
$realIds = (& $isGuid $e["AGENT365_AGENT_ID"]) -and (& $isGuid $e["AGENT365_TENANT_ID"])
Line $realIds "Agent / tenant IDs configured in .env" $(if (-not $realIds) { "-> placeholders local-dev-agent / local-dev-tenant. Fix: Configure-Entra.ps1 -IdsOnly" })

Write-Host "`n--- Export to Microsoft 365 (Defender / Purview / admin center) ---" -ForegroundColor Cyan
$checks = [ordered]@{
    "AUTH_ENABLED=true"                        = $e["AUTH_ENABLED"] -eq "true"
    "ENABLE_A365_OBSERVABILITY_EXPORTER=true"  = $e["ENABLE_A365_OBSERVABILITY_EXPORTER"] -eq "true"
    "AGENT365_TENANT_ID set"                   = [bool](& $isGuid $e["AGENT365_TENANT_ID"])
    "AGENT365_BLUEPRINT_ID set"                = [bool](& $isGuid $e["AGENT365_BLUEPRINT_ID"])
    "AGENT365_AGENT_ID set"                    = [bool](& $isGuid $e["AGENT365_AGENT_ID"])
    "AGENT365_BLUEPRINT_CLIENT_SECRET set"     = [bool]$e["AGENT365_BLUEPRINT_CLIENT_SECRET"]
    "AGENT365_BLUEPRINT_SCOPE resolved"        = $e["AGENT365_BLUEPRINT_SCOPE"] -like "api://*" -and $e["AGENT365_BLUEPRINT_SCOPE"] -notlike "*<*"
    "WEB_CLIENT_ID set"                        = [bool](& $isGuid $e["WEB_CLIENT_ID"])
    "WEB_CLIENT_SECRET set"                    = [bool]$e["WEB_CLIENT_SECRET"]
}
foreach ($k in $checks.Keys) { Line $checks[$k] $k }

if ($Tenant) {
    Write-Host "`n--- Tenant (read-only, a365 query-entra) ---" -ForegroundColor Cyan
    Push-Location $root
    try {
        $bp = (a365 query-entra blueprint-scopes 2>&1) -join "`n"
        $ai = (a365 query-entra instance-scopes 2>&1) -join "`n"
    } finally { Pop-Location }
    $inherit = [ordered]@{
        "Content.Process.User" = "Microsoft Graph"; "ContentActivity.Write" = "Microsoft Graph"
        "ProtectionScopes.Compute.User" = "Microsoft Graph"; "Agent365.Observability.OtelWrite" = "maven-prod"
        "Connectivity.Connections.Read" = "Power Platform API"
    }
    foreach ($s in $inherit.Keys) {
        $ok = $bp -match "(?m)^\s+$([regex]::Escape($s))\s*$"
        $checks["Blueprint inheritable $s"] = $ok
        Line $ok "Blueprint inheritable: $($inherit[$s]) $s" $(if (-not $ok) { "-> Fix: Configure-Entra.ps1 (step 'Blueprint inheritable permissions')" })
    }
    $ok = $ai -match "Agent365\.Observability\.OtelWrite"
    $checks["Agent identity OtelWrite grant"] = $ok
    Line $ok "Agent identity -> Agent365.Observability.OtelWrite"
}

$exporting = $checks.Values -notcontains $false
Write-Host ""
if ($exporting) {
    Write-Host " VERDICT: local configuration prerequisites are present; delivery is NOT verified." -ForegroundColor Green
    Write-Host "          'telemetry: token-ok' confirms token acquisition, not ingestion."
    Write-Host "          Check the exporter HTTP response and routing results, then the matching audit record."
    Write-Host "          Purview: DSPM > Discover > Activity explorer > AI activities (NOT the classic one)."
} else {
    Write-Host " VERDICT: local configuration prerequisites are incomplete." -ForegroundColor Red
    Write-Host "          This script reads .env, not the running process's environment."
    Write-Host ""
    Write-Host "          Fix:  .\scripts\Configure-Entra.ps1   (interactive, needs Global Admin)" -ForegroundColor Yellow
    Write-Host "          Then: .\scripts\run.ps1  and sign in at http://localhost:8000"
}
Write-Host ""
