<#
.SYNOPSIS
  Materialise the blueprint's inheritable permissions as explicit, tenant-wide grants on every agent identity.

.DESCRIPTION
  Blueprint inheritable permissions are evaluated at token time, so an agent identity can get a token for a
  scope even though the Entra admin center shows nothing under its "Permissions" blade (that blade only lists
  explicit oauth2PermissionGrants and app role assignments). This script makes the portal match the blueprint:

    * for every AllPrincipals delegated grant on the blueprint principal, create/extend the same AllPrincipals
      grant on each agent identity (only scopes the blueprint marks inheritable: enumerated list or allAllowed)
    * for every app role assigned to the blueprint principal, assign the same role to each agent identity
      (only when the blueprint marks roles for that resource inheritable)

  Agent identities are read from AGENT365_AGENT_ID in .env and every .env.agent* overlay (or -AgentIds).
  Idempotent: existing grants are extended, never narrowed; existing role assignments are skipped.
  Uses the az CLI Graph token (DelegatedPermissionGrant.ReadWrite.All, AppRoleAssignment.ReadWrite.All).
  Does not call the a365 CLI.

.EXAMPLE
  .\scripts\Sync-AgentPermissions.ps1 -WhatIf
  .\scripts\Sync-AgentPermissions.ps1
#>
[CmdletBinding()]
param(
    [string]$BlueprintId,
    [string[]]$AgentIds,
    [switch]$WhatIf
)
$ErrorActionPreference = "Stop"
$root = Split-Path $PSScriptRoot -Parent

function Read-DotEnv([string]$path) {
    $h = @{}
    if (Test-Path $path) {
        Get-Content $path | Where-Object { $_ -match '^\s*([A-Za-z0-9_]+)\s*=\s*(.*)$' } | ForEach-Object {
            $h[$Matches[1]] = $Matches[2].Trim().Trim('"')
        }
    }
    $h
}

$envFiles = @(Join-Path $root ".env") + @(Get-ChildItem $root -Force -Filter ".env.agent*" |
    Where-Object { $_.Name -notlike "*.example" } | ForEach-Object FullName)
$base = Read-DotEnv (Join-Path $root ".env")
if (-not $BlueprintId) { $BlueprintId = $base["AGENT365_BLUEPRINT_ID"] }
if (-not $BlueprintId) { throw "AGENT365_BLUEPRINT_ID not found in .env - pass -BlueprintId." }
if (-not $AgentIds) {
    $AgentIds = $envFiles | ForEach-Object { (Read-DotEnv $_)["AGENT365_AGENT_ID"] } | Where-Object { $_ } | Select-Object -Unique
}
if (-not $AgentIds) { throw "No AGENT365_AGENT_ID found in .env / .env.agent* - pass -AgentIds." }

$token = az account get-access-token --resource-type ms-graph --query accessToken -o tsv
if (-not $token) { throw "az login first (az account get-access-token failed)." }
$headers = @{ Authorization = "Bearer $token"; "Content-Type" = "application/json" }
function Graph([string]$method, [string]$uri, $body) {
    $p = @{ Method = $method; Uri = "https://graph.microsoft.com$uri"; Headers = $headers }
    if ($null -ne $body) { $p.Body = ($body | ConvertTo-Json -Depth 8) }
    Invoke-RestMethod @p
}
function Get-SpByAppId([string]$appId) {
    (Graph GET "/v1.0/servicePrincipals?`$filter=appId eq '$appId'&`$select=id,appId,displayName").value | Select-Object -First 1
}

Write-Host "==> Blueprint $BlueprintId" -ForegroundColor Cyan
$bpSp = Get-SpByAppId $BlueprintId
if (-not $bpSp) { throw "Blueprint service principal not found." }
$inherit = @{}
(Graph GET "/beta/applications/microsoft.graph.agentIdentityBlueprint(appId='$BlueprintId')/inheritablePermissions").value |
    ForEach-Object { $inherit[$_.resourceAppId] = $_ }

$resCache = @{}
function Resource([string]$spId) {
    if (-not $resCache[$spId]) { $resCache[$spId] = Graph GET "/v1.0/servicePrincipals/$spId`?`$select=id,appId,displayName" }
    $resCache[$spId]
}

# Delegated: blueprint AllPrincipals grants, filtered to inheritable scopes
$wantScopes = @()
foreach ($g in (Graph GET "/v1.0/oauth2PermissionGrants?`$filter=clientId eq '$($bpSp.id)'").value | Where-Object consentType -eq "AllPrincipals") {
    $res = Resource $g.resourceId
    $ip = $inherit[$res.appId]
    if (-not $ip) { Write-Host "    skip $($res.displayName): not inheritable" -ForegroundColor DarkGray; continue }
    $scopes = @($g.scope -split '\s+' | Where-Object { $_ })
    if ($ip.inheritableScopes.kind -eq "enumerated") { $scopes = @($scopes | Where-Object { $_ -in $ip.inheritableScopes.scopes }) }
    elseif ($ip.inheritableScopes.kind -ne "allAllowed") { $scopes = @() }
    if ($scopes) { $wantScopes += [pscustomobject]@{ Resource = $res; Scopes = $scopes } }
}
# Application: blueprint app role assignments, filtered to inheritable roles
$wantRoles = @()
foreach ($a in (Graph GET "/v1.0/servicePrincipals/$($bpSp.id)/appRoleAssignments").value) {
    $res = Resource $a.resourceId
    $ip = $inherit[$res.appId]
    $ok = $ip -and ($ip.inheritableRoles.kind -eq "allAllowed" -or ($ip.inheritableRoles.kind -eq "enumerated" -and $a.appRoleId -in $ip.inheritableRoles.roles))
    if ($ok) { $wantRoles += [pscustomobject]@{ Resource = $res; RoleId = $a.appRoleId } }
    else { Write-Host "    skip role $($a.appRoleId) on $($res.displayName): not inheritable" -ForegroundColor DarkGray }
}
$wantScopes | ForEach-Object { Write-Host "    delegated  $($_.Resource.displayName): $($_.Scopes -join ' ')" }
$wantRoles  | ForEach-Object { Write-Host "    app role   $($_.Resource.displayName): $($_.RoleId)" }

foreach ($agentId in $AgentIds) {
    $ai = Get-SpByAppId $agentId
    if (-not $ai) { Write-Warning "Agent identity $agentId not found - skipped"; continue }
    Write-Host "`n==> $($ai.displayName) ($agentId)" -ForegroundColor Cyan
    foreach ($w in $wantScopes) {
        $existing = (Graph GET "/v1.0/oauth2PermissionGrants?`$filter=clientId eq '$($ai.id)' and resourceId eq '$($w.Resource.id)'").value |
            Where-Object consentType -eq "AllPrincipals" | Select-Object -First 1
        if ($existing) {
            $have = @($existing.scope -split '\s+' | Where-Object { $_ })
            $missing = @($w.Scopes | Where-Object { $_ -notin $have })
            if (-not $missing) { Write-Host "    ok       $($w.Resource.displayName): $($w.Scopes -join ' ')" -ForegroundColor DarkGray; continue }
            Write-Host "    extend   $($w.Resource.displayName): + $($missing -join ' ')" -ForegroundColor Green
            if (-not $WhatIf) { Graph PATCH "/v1.0/oauth2PermissionGrants/$($existing.id)" @{ scope = (($have + $missing) -join ' ') } | Out-Null }
        } else {
            Write-Host "    grant    $($w.Resource.displayName): $($w.Scopes -join ' ') (all users)" -ForegroundColor Green
            if (-not $WhatIf) {
                Graph POST "/v1.0/oauth2PermissionGrants" @{ clientId = $ai.id; consentType = "AllPrincipals"; resourceId = $w.Resource.id; scope = ($w.Scopes -join ' ') } | Out-Null
            }
        }
    }
    $assigned = @((Graph GET "/v1.0/servicePrincipals/$($ai.id)/appRoleAssignments").value)
    foreach ($w in $wantRoles) {
        if ($assigned | Where-Object { $_.resourceId -eq $w.Resource.id -and $_.appRoleId -eq $w.RoleId }) {
            Write-Host "    ok       role $($w.Resource.displayName): $($w.RoleId)" -ForegroundColor DarkGray; continue
        }
        Write-Host "    assign   role $($w.Resource.displayName): $($w.RoleId)" -ForegroundColor Green
        if (-not $WhatIf) {
            Graph POST "/v1.0/servicePrincipals/$($ai.id)/appRoleAssignments" @{ principalId = $ai.id; resourceId = $w.Resource.id; appRoleId = $w.RoleId } | Out-Null
        }
    }
}
Write-Host "`nDone$(if ($WhatIf) { ' (WhatIf - nothing changed)' }). Entra admin center > Enterprise applications > <agent> > Permissions now lists these grants." -ForegroundColor Green
