<#
.SYNOPSIS
  Creates the demo Purview DLP policy for the agent: prompts that contain a "restricted city" (e.g. Dallas) are
  BLOCKED; everything else (e.g. San Francisco) is allowed. Enforced by the agent via Graph processContent.

.DESCRIPTION
  Entra-registered apps/agents can only be targeted from Security & Compliance PowerShell (the Purview portal UI
  doesn't support this location yet): https://learn.microsoft.com/purview/developer/use-the-api

  The policy location is the app ID that the agent sends as protectedAppMetadata.applicationLocation
  (PURVIEW_APP_LOCATION_ID, default = AGENT365_AGENT_ID, the agent identity). They MUST match.

  Prerequisite: a custom sensitive information type (default name "Demo - Restricted City") with keyword "Dallas".
  Create it in Purview portal > Information protection > Classifiers > Sensitive info types > Create
  (Primary element: Keyword list "Dallas", case-insensitive, confidence High). Allow ~15 minutes to replicate.

.EXAMPLE
  .\scripts\New-PurviewDlpDemo.ps1 -WhatIf
  .\scripts\New-PurviewDlpDemo.ps1 -UserPrincipalName admin@contoso.onmicrosoft.com
#>
[CmdletBinding(SupportsShouldProcess)]
param(
    [string]$UserPrincipalName,
    [string]$SensitiveInfoType = 'Demo - Restricted City',
    [string]$PolicyName = 'Demo - Agent restricted city (Dallas)',
    [string]$RuleName = 'Block restricted city in agent prompts and responses',
    [switch]$BlockResponses = $true,
    [string]$AppId,
    [string]$AppName
)
$ErrorActionPreference = 'Stop'
$root = Split-Path -Parent $PSScriptRoot
$envMap = @{}
Get-Content (Join-Path $root '.env') -ErrorAction SilentlyContinue | ForEach-Object {
    if ($_ -match '^\s*([A-Za-z0-9_]+)\s*=(.*)$') { $envMap[$matches[1]] = $matches[2].Trim().Trim('"') }
}
if (-not $AppId) { $AppId = if ($envMap.PURVIEW_APP_LOCATION_ID) { $envMap.PURVIEW_APP_LOCATION_ID } else { $envMap.AGENT365_AGENT_ID } }
if (-not $AppName) { $AppName = if ($envMap.AGENT365_AGENT_NAME) { $envMap.AGENT365_AGENT_NAME } else { 'Agent' } }
if (-not $AppId) { throw 'No app ID: set AGENT365_AGENT_ID (or PURVIEW_APP_LOCATION_ID) in .env, or pass -AppId.' }

# Every agent instance of the blueprint (.env + .env.agent* overlays from New-AgentInstance.ps1) is one app location.
$apps = [ordered]@{ $AppId = $AppName }
Get-ChildItem $root -Filter '.env.agent*' -File -ErrorAction SilentlyContinue | Where-Object Name -notlike '*.example' | ForEach-Object {
    $o = @{}
    Get-Content $_.FullName | ForEach-Object { if ($_ -match '^\s*([A-Za-z0-9_]+)\s*=(.*)$') { $o[$matches[1]] = $matches[2].Trim().Trim('"') } }
    $id = if ($o.PURVIEW_APP_LOCATION_ID) { $o.PURVIEW_APP_LOCATION_ID } else { $o.AGENT365_AGENT_ID }
    if ($id -and -not $apps.Contains($id)) { $apps[$id] = if ($o.AGENT365_AGENT_NAME) { $o.AGENT365_AGENT_NAME } else { $_.Name } }
}
$locations = ConvertTo-Json -Compress -Depth 5 @($apps.GetEnumerator() | ForEach-Object { @{
    Workload = 'Applications'; Location = $_.Key; LocationDisplayName = $_.Value
    LocationSource = 'Entra'; LocationType = 'Individual'
    Inclusions = @(@{ Type = 'Tenant'; Identity = 'All' })
} })
$restrict = @(@{ setting = 'UploadText'; value = 'Block' })
if ($BlockResponses) { $restrict += @{ setting = 'DownloadText'; value = 'Block' } }

Write-Host "==> DLP policy '$PolicyName'" -ForegroundColor Cyan
$apps.GetEnumerator() | ForEach-Object { Write-Host "    Location (Entra app): $($_.Value) ($($_.Key))" }
Write-Host "    Condition:            content contains SIT '$SensitiveInfoType'"
Write-Host "    Action:               RestrictAccess $(($restrict | ForEach-Object { "$($_.setting)=$($_.value)" }) -join ', ')"

if (-not $WhatIfPreference) {
    if (-not (Get-Command Connect-IPPSSession -ErrorAction SilentlyContinue)) {
        throw "ExchangeOnlineManagement module missing: Install-Module ExchangeOnlineManagement -Scope CurrentUser"
    }
    $connect = @{ ShowBanner = $false }
    if ($UserPrincipalName) { $connect.UserPrincipalName = $UserPrincipalName }
    Connect-IPPSSession @connect
    if (-not (Get-DlpSensitiveInformationType -Identity $SensitiveInfoType -ErrorAction SilentlyContinue)) {
        throw "Sensitive info type '$SensitiveInfoType' not found. Create it first (see .DESCRIPTION)."
    }
}

if (Get-Command Get-DlpCompliancePolicy -ErrorAction SilentlyContinue) {
    $existing = Get-DlpCompliancePolicy -Identity $PolicyName -ErrorAction SilentlyContinue
}
if ($existing) {
    # -Locations replaces the whole set, so passing every instance is idempotent and picks up new agents.
    if ($PSCmdlet.ShouldProcess($PolicyName, "Set-DlpCompliancePolicy -Locations ($($apps.Count) agent app(s))")) {
        Set-DlpCompliancePolicy -Identity $PolicyName -Locations $locations | Out-Null
        Write-Host "    Policy exists - locations updated to $($apps.Count) agent app(s)" -ForegroundColor Yellow
    }
} elseif ($PSCmdlet.ShouldProcess($PolicyName, 'New-DlpCompliancePolicy (Applications / Entra)')) {
    New-DlpCompliancePolicy -Name $PolicyName -Mode Enable -Locations $locations -EnforcementPlanes @('Application') | Out-Null
    Write-Host "    Policy created" -ForegroundColor Green
}

if (Get-Command Get-DlpComplianceRule -ErrorAction SilentlyContinue) {
    $rule = Get-DlpComplianceRule -Identity $RuleName -ErrorAction SilentlyContinue
}
if ($rule) {
    Write-Host "    Rule exists - keeping it" -ForegroundColor Yellow
} elseif ($PSCmdlet.ShouldProcess($RuleName, 'New-DlpComplianceRule (RestrictAccess Block)')) {
    New-DlpComplianceRule -Name $RuleName -Policy $PolicyName `
        -ContentContainsSensitiveInformation @{ Name = $SensitiveInfoType } `
        -RestrictAccess $restrict -GenerateAlert $true | Out-Null
    Write-Host "    Rule created" -ForegroundColor Green
}

Write-Host ""
Write-Host "Policy sync can take up to ~1 hour. Check it's applied to the agent:" -ForegroundColor Cyan
Write-Host "  1. Sign in at http://localhost:8000 (agent 2: :8001), then open /api/purview/scopes on the same port"
Write-Host "     -> modes.uploadText should be 'evaluateInline'"
Write-Host "  2. Ask 'weather in San Francisco' (allowed) then 'weather in Dallas' (blocked)."
