<#
.SYNOPSIS
  Post-registration Entra wiring for the demo (run AFTER `a365 setup all`).

  1. Reuses the user scope `a365 setup` exposes on the blueprint (api://<blueprintAppId>/access_agent_as_user), or creates one
  2. Adds a demo client secret to the blueprint (skipped if one is already in .env / generated config)
  3. Creates the "<agent>-WebClient" app registration the browser UI signs users into
     (redirect URI, secret, delegated permission to the blueprint scope, tenant-wide admin consent)
  4. Grants the agent identity tenant-wide delegated consent to Agent365.Observability.OtelWrite
  4b. Configures inheritable blueprint permissions (via the a365 CLI) so agent identities inherit them:
      Graph Content.Process.User / ContentActivity.Write / ProtectionScopes.Compute.User (Purview SDK),
      Agent365.Observability.OtelWrite, Power Platform Connectivity.Connections.Read
  5. Writes every value into .env and switches AUTH_ENABLED / ENABLE_A365_OBSERVABILITY_EXPORTER to true

  Idempotent: re-running reuses existing objects and only creates secrets that are missing from .env.

.PARAMETER GeneratedConfig   Path to a365.generated.config.json (default: repo root)
.PARAMETER AgentId           Agent identity app ID, if it can't be discovered automatically
.PARAMETER WhatIf            Show what would happen without changing Entra or .env
.PARAMETER IdsOnly           No Graph calls and no admin sign-in. Copies the real tenant / blueprint / agent IDs
                             from a365.generated.config.json into .env so local Jaeger traces carry the real
                             gen_ai.agent.id and microsoft.tenant.id. Leaves AUTH_ENABLED and the Agent 365
                             exporter off - those still need the full run.
.PARAMETER UseAzCli          Use the existing `az login` session instead of an interactive Connect-MgGraph
                             prompt. Requires `az login --tenant <domain> --allow-no-subscriptions` as a
                             Global/Application Administrator. The Azure CLI token already carries
                             Application.ReadWrite.All and DelegatedPermissionGrant.ReadWrite.All, which is
                             enough as long as the blueprint scope and secret already exist (they do after
                             `a365 setup all`).
#>
[CmdletBinding()]
param(
    [string]$GeneratedConfig,
    [string]$AgentId,
    [switch]$WhatIf,
    [switch]$IdsOnly,
    [switch]$UseAzCli
)
$ErrorActionPreference = "Stop"
$root = Split-Path $PSScriptRoot -Parent
$envFile = Join-Path $root ".env"
if (-not $GeneratedConfig) { $GeneratedConfig = Join-Path $root "a365.generated.config.json" }

$ScopeValue = "access_agent_as_user"   # name the a365 CLI uses
$GraphAppId = "00000003-0000-0000-c000-000000000000"
# Delegated scopes configured as INHERITABLE blueprint permissions with tenant-wide consent, so every agent
# identity created from the blueprint shows them as "Inherited from parent" (Entra > Agent ID > identity > Permissions).
$InheritablePermissions = [ordered]@{
    # Purview SDK (processContent / protectionScopes / contentActivities)
    "00000003-0000-0000-c000-000000000000" = @("Content.Process.User", "ContentActivity.Write", "ProtectionScopes.Compute.User")
    # Agent 365 Observability (exporter OBO token)
    "9b975845-388f-4429-889e-eab1ef63949c" = @("Agent365.Observability.OtelWrite")
    # Power Platform API (a365 CLI developer default for OBO agents)
    "8578e004-a5c6-46e7-913e-12f58912df43" = @("Connectivity.Connections.Read")
}
$GraphScopes = @{   # well-known delegated permission IDs on Microsoft Graph
    "openid"         = "37f7f235-527c-4136-accd-4a02d197296e"
    "profile"        = "14dad69e-099b-42c9-810b-d002981feec1"
    "offline_access" = "7427e0e9-2fba-42fe-b0c0-848c9e6a8182"
    "User.Read"      = "e1fe6dd8-ba31-4d61-89e7-88639da4683d"
}

function Step($m) { Write-Host "==> $m" -ForegroundColor Cyan }
function Info($m) { Write-Host "    $m" }

# ── .env helpers ─────────────────────────────────────────────────────────────
function Read-DotEnv {
    $h = [ordered]@{}
    if (Test-Path $envFile) {
        foreach ($l in Get-Content $envFile) {
            if ($l -match '^\s*([A-Za-z0-9_]+)\s*=(.*)$') { $h[$matches[1]] = $matches[2].Trim() }
        }
    }
    $h
}
function Set-DotEnv([hashtable]$values) {
    if (-not (Test-Path $envFile)) { Copy-Item (Join-Path $root ".env.example") $envFile }
    $lines = [System.Collections.Generic.List[string]](Get-Content $envFile)
    foreach ($k in $values.Keys) {
        $idx = -1
        for ($i = 0; $i -lt $lines.Count; $i++) { if ($lines[$i] -match "^\s*$k\s*=") { $idx = $i; break } }
        $line = "$k=$($values[$k])"
        if ($idx -ge 0) { $lines[$idx] = $line } else { $lines.Add($line) }
    }
    Set-Content $envFile $lines -Encoding utf8
}

function First-Value($obj, [string[]]$names) {
    foreach ($n in $names) {
        $p = $obj.PSObject.Properties | Where-Object { $_.Name -ieq $n } | Select-Object -First 1
        if ($p -and $p.Value -and "$($p.Value)".Trim()) { return "$($p.Value)".Trim() }
    }
    $null
}

# ── 0. Inputs ────────────────────────────────────────────────────────────────
Step "Reading $GeneratedConfig"
if (-not (Test-Path $GeneratedConfig)) {
    throw "Not found. Run 'a365 setup all --agent-name <name> --authmode obo' from the repo root first (docs\02-register.md)."
}
$gen = Get-Content $GeneratedConfig -Raw | ConvertFrom-Json
$cur = Read-DotEnv

$tenantId    = First-Value $gen @("tenantId")
if (-not $tenantId) {
    $cfgPath = Join-Path (Split-Path $GeneratedConfig -Parent) "a365.config.json"
    if (Test-Path $cfgPath) { $tenantId = First-Value (Get-Content $cfgPath -Raw | ConvertFrom-Json) @("tenantId") }
}
if (-not $tenantId) { try { $tenantId = (az account show --query tenantId -o tsv 2>$null) } catch {} }
$blueprintId = First-Value $gen @("agentBlueprintId", "blueprintId")
if (-not $AgentId) { $AgentId = First-Value $gen @("agentIdentityClientId", "agentIdentityAppId", "agenticAppId", "agentId") }
$genSecret   = First-Value $gen @("agentBlueprintClientSecret")
$secretIsProtected = $gen.agentBlueprintClientSecretProtected -eq $true   # DPAPI-encrypted by the CLI -> not usable here
if (-not $tenantId)    { $tenantId = $cur["AGENT365_TENANT_ID"] }
if (-not $tenantId -or -not $blueprintId) { throw "tenantId / agentBlueprintId not found in a365.generated.config.json, a365.config.json or 'az account show'." }
if ($gen.completed -eq $false) { Write-Host "    Note: completed=false in a365.generated.config.json can remain after a successful blueprint-only (OBO) run; verify with 'a365 setup all --dry-run' (see docs\02-register.md)." -ForegroundColor DarkGray }
$agentName = if ($cur["AGENT365_AGENT_NAME"]) { $cur["AGENT365_AGENT_NAME"] } else { "Laszlo-AgentRegistryDemo1" }
Info "Tenant:    $tenantId"
Info "Blueprint: $blueprintId"

if ($IdsOnly) {
    if (-not $AgentId) { throw "Agent identity ID not found in the generated config. Re-run with -AgentId <appId> (Entra admin center > Agent ID > All agent identities)." }
    Step "Writing real IDs to .env (no Graph calls, no tenant changes)"
    $idVals = @{
        AGENT365_TENANT_ID    = $tenantId
        AGENT365_BLUEPRINT_ID = $blueprintId
        AGENT365_AGENT_ID     = $AgentId
    }
    if ($WhatIf) { $idVals.GetEnumerator() | Sort-Object Name | ForEach-Object { Info "[WhatIf] $($_.Key)=$($_.Value)" } }
    else {
        Set-DotEnv $idVals
        $idVals.GetEnumerator() | Sort-Object Name | ForEach-Object { Info "$($_.Key)=$($_.Value)" }
    }
    Write-Host ""
    Info "Jaeger will now show the real gen_ai.agent.id / microsoft.tenant.id."
    Info "AUTH_ENABLED and the Agent 365 exporter stay OFF - run Configure-Entra.ps1 without -IdsOnly for those."
    return
}

# ── 1. Connect ───────────────────────────────────────────────────────────────
$GraphBase = "https://graph.microsoft.com"
if ($UseAzCli) {
    Step "Using the existing 'az login' session (no interactive prompt)"
    $acct = az account show 2>$null | ConvertFrom-Json
    if (-not $acct) { throw "Not signed in. Run: az login --tenant <domain> --allow-no-subscriptions" }
    if ($acct.tenantId -ne $tenantId) { throw "az is signed in to tenant $($acct.tenantId) but the config says $tenantId." }
    Info "Signed in as $($acct.user.name)"
    # Call Graph directly with the az token: passing URLs through az.cmd breaks on '(' and '?' (cmd parsing)
    $azToken = az account get-access-token --resource $GraphBase --query accessToken -o tsv 2>$null
    if (-not $azToken) { throw "Could not get a Microsoft Graph token from the az session." }
    $script:azHeaders = @{ Authorization = "Bearer $azToken"; "Content-Type" = "application/json" }

    function Graph([string]$method, [string]$uri, $body) {
        if ($WhatIf -and $method -ne "GET") { Info "[WhatIf] $method $uri"; return $null }
        $full = "$GraphBase$uri"
        $p = @{ Method = $method; Uri = $full; Headers = $script:azHeaders }
        if ($body) { $p.Body = ($body | ConvertTo-Json -Depth 10) }
        try { return Invoke-RestMethod @p }
        catch {
            $detail = $_.ErrorDetails.Message
            if (-not $detail) { $detail = $_.Exception.Message }
            throw "$method $full -> $detail"
        }
    }
} else {
    Step "Connecting to Microsoft Graph (sign in as a Global / Application Administrator)"
    Import-Module Microsoft.Graph.Authentication
    Connect-MgGraph -TenantId $tenantId -NoWelcome -Scopes @(
        "Application.ReadWrite.All",
        "AgentIdentityBlueprint.UpdateAuthProperties.All",
        "AgentIdentityBlueprint.AddRemoveCreds.All",
        "DelegatedPermissionGrant.ReadWrite.All"
    )
    function Graph([string]$method, [string]$uri, $body) {
        if ($WhatIf -and $method -ne "GET") { Info "[WhatIf] $method $uri"; return $null }
        $req = @{ Method = $method; Uri = "$GraphBase$uri"; Headers = @{ "OData-Version" = "4.0" } }
        if ($body) { $req.Body = ($body | ConvertTo-Json -Depth 10); $req.ContentType = "application/json" }
        Invoke-MgGraphRequest @req -OutputType PSObject
    }
}

# ── 2. Agent identity ────────────────────────────────────────────────────────
if (-not $AgentId) {
    Step "Discovering agent identity created from the blueprint"
    $ids = Graph GET "/beta/servicePrincipals/microsoft.graph.agentIdentity?`$filter=agentIdentityBlueprintId eq '$blueprintId'&`$select=appId,id,displayName"
    $AgentId = ($ids.value | Select-Object -First 1).appId
    if (-not $AgentId) { throw "No agent identity found for blueprint. Pass -AgentId <appId> (see Entra admin center > Agent ID > Agents)." }
}
Info "Agent identity (gen_ai.agent.id): $AgentId"

# ── 3. User scope on the blueprint ────────────────────────────────
Step "Exposing a user scope on the blueprint (api://$blueprintId)"
$bp = Graph GET "/v1.0/applications(appId='$blueprintId')?`$select=id,appId,displayName,identifierUris,api"
$scopes = @($bp.api.oauth2PermissionScopes | Where-Object { $_ })
# Prefer our scope, then any enabled User scope the a365 CLI already created (e.g. access_agent_as_user)
$scope = $scopes | Where-Object { $_.value -eq $ScopeValue } | Select-Object -First 1
if (-not $scope) { $scope = $scopes | Where-Object { $_.isEnabled -and $_.type -eq "User" } | Select-Object -First 1 }
if ($scope) {
    $ScopeValue = $scope.value
    Info "Using existing scope '$ScopeValue' ($($scope.id))"
    $scopeId = $scope.id
} else {
    $scopeId = [guid]::NewGuid().ToString()
    $newScopes = @($scopes | Where-Object { $_ }) + @(@{
        adminConsentDescription = "Allow the application to access $agentName on behalf of the signed-in user."
        adminConsentDisplayName = "Access $agentName"
        userConsentDescription  = "Allow the application to access $agentName on your behalf."
        userConsentDisplayName  = "Access $agentName"
        id = $scopeId; isEnabled = $true; type = "User"; value = $ScopeValue
    })
    $uris = @($bp.identifierUris) + @("api://$blueprintId") | Where-Object { $_ } | Select-Object -Unique
    Graph PATCH "/v1.0/applications/$($bp.id)" @{ identifierUris = @($uris); api = @{ oauth2PermissionScopes = $newScopes } } | Out-Null
    Info "Created scope $scopeId"
}

# ── 4. Blueprint client secret (demo only) ───────────────────────────────────
Step "Blueprint client secret"
$bpSecret = $cur["AGENT365_BLUEPRINT_CLIENT_SECRET"]
if (-not $bpSecret -and $genSecret -and -not $secretIsProtected) { $bpSecret = $genSecret; Info "Using secret from generated config" }
if (-not $bpSecret) {
    # `a365 setup all` writes the blueprint secret into .env under its own key - reuse it instead of minting another
    $cliSecret = $cur["CONNECTIONS__SERVICE_CONNECTION__SETTINGS__CLIENTSECRET"]
    if (-not $cliSecret) { $cliSecret = $cur["AGENT365OBSERVABILITY__CLIENTSECRET"] }
    if ($cliSecret -and $cur["CONNECTIONS__SERVICE_CONNECTION__SETTINGS__CLIENTID"] -eq $blueprintId) {
        $bpSecret = $cliSecret.Trim('"')
        Info "Reusing the blueprint secret 'a365 setup all' wrote to .env"
    }
}
if (-not $bpSecret) {
    $pw = Graph POST "/v1.0/applications/$($bp.id)/addPassword" @{ passwordCredential = @{ displayName = "local-demo"; endDateTime = (Get-Date).AddDays(30).ToString("o") } }
    if ($WhatIf) { $bpSecret = "(generated at run time)" }
    else { $bpSecret = $pw.secretText; Info "Created 30-day secret 'local-demo'" }
} else { Info "Secret already configured - keeping it" }

# ── 5. Web client app registration ───────────────────────────────────────────
$webName = "$agentName-WebClient"
Step "Web client app registration '$webName'"
$redirect = if ($cur["WEB_REDIRECT_URI"]) { $cur["WEB_REDIRECT_URI"] } else { "http://localhost:8000/auth/callback" }
$rra = @(
    @{ resourceAppId = $blueprintId; resourceAccess = @(@{ id = $scopeId; type = "Scope" }) },
    @{ resourceAppId = $GraphAppId;  resourceAccess = @($GraphScopes.Values | ForEach-Object { @{ id = $_; type = "Scope" } }) }
)
$existing = (Graph GET "/v1.0/applications?`$filter=displayName eq '$webName'&`$select=id,appId").value | Select-Object -First 1
if ($existing) {
    $web = $existing
    Graph PATCH "/v1.0/applications/$($web.id)" @{ web = @{ redirectUris = @($redirect) }; requiredResourceAccess = $rra } | Out-Null
    Info "Reusing $($web.appId) (redirect + permissions refreshed)"
} else {
    $web = Graph POST "/v1.0/applications" @{
        displayName = $webName; signInAudience = "AzureADMyOrg"
        web = @{ redirectUris = @($redirect) }
        requiredResourceAccess = $rra
    }
    if (-not $WhatIf) { Info "Created $($web.appId)" }
}
$pendingWebApp = $WhatIf -and -not $web
if ($pendingWebApp) { $web = [pscustomobject]@{ id = "(not created in -WhatIf)"; appId = "(not created in -WhatIf)" } }

if ($pendingWebApp) {
    Info "[WhatIf] would create its service principal, a 30-day secret, and tenant-wide consent"
    $webSp = [pscustomobject]@{ id = "(not created in -WhatIf)" }
    $webSecret = "(generated at run time)"
} else {
    $webSp = (Graph GET "/v1.0/servicePrincipals?`$filter=appId eq '$($web.appId)'&`$select=id").value | Select-Object -First 1
    if (-not $webSp) { $webSp = Graph POST "/v1.0/servicePrincipals" @{ appId = $web.appId } }

    $webSecret = if ($cur["WEB_CLIENT_ID"] -eq $web.appId) { $cur["WEB_CLIENT_SECRET"] } else { $null }
    if (-not $webSecret) {
        $pw = Graph POST "/v1.0/applications/$($web.id)/addPassword" @{ passwordCredential = @{ displayName = "local-demo"; endDateTime = (Get-Date).AddDays(30).ToString("o") } }
        $webSecret = $pw.secretText
        Info "Created 30-day web client secret"
    }
}

# ── 6. Tenant-wide admin consent (so demo users never see a consent prompt) ──
Step "Granting admin consent"
function Grant([string]$resourceAppId, [string]$scopeString) {
    $res = (Graph GET "/v1.0/servicePrincipals?`$filter=appId eq '$resourceAppId'&`$select=id").value | Select-Object -First 1
    if (-not $res) { throw "Service principal for $resourceAppId not found (blueprint principal missing? re-run a365 setup blueprint)." }
    $g = (Graph GET "/v1.0/oauth2PermissionGrants?`$filter=clientId eq '$($webSp.id)' and resourceId eq '$($res.id)'").value | Select-Object -First 1
    if ($g) {
        Graph PATCH "/v1.0/oauth2PermissionGrants/$($g.id)" @{ scope = $scopeString } | Out-Null
    } else {
        Graph POST "/v1.0/oauth2PermissionGrants" @{ clientId = $webSp.id; consentType = "AllPrincipals"; resourceId = $res.id; scope = $scopeString } | Out-Null
    }
    Info "$resourceAppId : $scopeString"
}
if (-not $WhatIf) {
    Grant $blueprintId $ScopeValue
    Grant $GraphAppId (($GraphScopes.Keys | Sort-Object) -join " ")

    # a365 --authmode obo creates principal-scoped grants (the admin who ran setup). Make the agent identity's
    # OBO to the Agent 365 Observability API work for every demo user (Alice, Bob, ...) too.
    # This grant is REQUIRED for telemetry: without it the exporter gets 403 and Defender/Purview stay empty.
    $otelGranted = $false
    try {
        $aiSp = (Graph GET "/v1.0/servicePrincipals?`$filter=appId eq '$AgentId'&`$select=id").value | Select-Object -First 1
        $obs  = (Graph GET "/v1.0/servicePrincipals?`$filter=appId eq '9b975845-388f-4429-889e-eab1ef63949c'&`$select=id").value | Select-Object -First 1
        if (-not $aiSp) { throw "Agent identity service principal not found for appId $AgentId." }
        if (-not $obs)  { throw "Agent 365 Observability service principal not found in this tenant." }
        # consentType does NOT support $filter - query by clientId/resourceId only, then match in PowerShell
        $grants = (Graph GET "/v1.0/oauth2PermissionGrants?`$filter=clientId eq '$($aiSp.id)' and resourceId eq '$($obs.id)'").value
        $g = $grants | Where-Object { $_.consentType -eq "AllPrincipals" } | Select-Object -First 1
        if (-not $g) {
            Graph POST "/v1.0/oauth2PermissionGrants" @{ clientId = $aiSp.id; consentType = "AllPrincipals"; resourceId = $obs.id; scope = "Agent365.Observability.OtelWrite" } | Out-Null
        }
        Info "Agent identity -> Agent 365 Observability : Agent365.Observability.OtelWrite (all users)"
        $otelGranted = $true
    } catch {
        Write-Host ""
        Write-Host "  ****************************************************************" -ForegroundColor Red
        Write-Host "   FAILED to grant Agent365.Observability.OtelWrite." -ForegroundColor Red
        Write-Host "   Without it the exporter gets HTTP 403 and NOTHING will appear" -ForegroundColor Red
        Write-Host "   in Defender, Purview or the M365 admin center." -ForegroundColor Red
        Write-Host "   $($_.Exception.Message)" -ForegroundColor Red
        Write-Host ""
        Write-Host "   Grant it manually: Entra admin center > Enterprise applications" -ForegroundColor Yellow
        Write-Host "   > '$agentName Identity' > Permissions > Grant admin consent" -ForegroundColor Yellow
        Write-Host "  ****************************************************************" -ForegroundColor Red
        Write-Host ""
    }
}

# ── 6b. Blueprint inheritable permissions (agent identities inherit them) ────
# Inheritable permissions need AgentIdentityBlueprint.UpdateAuthProperties.All, which the az / Connect-MgGraph
# tokens above don't carry, so delegate to the a365 CLI (it also creates the AllPrincipals grant and verifies).
# Re-runs are safe: the CLI reports "already configured".
Step "Blueprint inheritable permissions"
$inheritOk = $true
if (-not (Get-Command a365 -ErrorAction SilentlyContinue)) {
    Write-Warning "a365 CLI not found - run 'a365 setup permissions custom --resource-app-id <id> --scopes <s1,s2>' for each resource below"
    $InheritablePermissions.GetEnumerator() | ForEach-Object { Info "$($_.Key) : $($_.Value -join ',')" }
    $inheritOk = $false
} else {
    foreach ($kv in $InheritablePermissions.GetEnumerator()) {
        $scopeCsv = $kv.Value -join ","
        $cliArgs = @("setup", "permissions", "custom", "--resource-app-id", $kv.Key, "--scopes", $scopeCsv)
        if ($WhatIf) { $cliArgs += "--dry-run" }
        Push-Location (Split-Path $GeneratedConfig -Parent)   # the CLI resolves a365.config.json from the cwd
        try { $out = & a365 @cliArgs 2>&1; $code = $LASTEXITCODE } finally { Pop-Location }
        if ($code -eq 0 -and ($WhatIf -or ($out -match 'Verified: .* inheritable permissions correctly configured'))) {
            Info "$(if ($WhatIf) { '[WhatIf] ' })$($kv.Key) : $($kv.Value -join ' ') (inheritable, admin consented)"
        } else {
            $inheritOk = $false
            $out | Where-Object { "$_" -match 'ERROR|Error|fail' } | ForEach-Object { Info "$_".Trim() }
            Write-Host "    FAILED (exit $code): a365 setup permissions custom --resource-app-id $($kv.Key) --scopes `"$scopeCsv`" -v" -ForegroundColor Red
        }
    }
}

# ── 6c. Mirror blueprint grants onto the agent identity ─────────────────────
# Inheritance is resolved at token time; the Entra admin center Permissions blade only lists explicit grants.
if ($inheritOk -and $AgentId) {
    Step "Mirroring blueprint grants onto the agent identity"
    & (Join-Path $PSScriptRoot "Sync-AgentPermissions.ps1") -BlueprintId $blueprintId -AgentIds $AgentId -WhatIf:$WhatIf
}

# ── 7. .env ──────────────────────────────────────────────────────────────────
Step "Updating .env"
$values = @{
    AGENT365_TENANT_ID                 = $tenantId
    AGENT365_BLUEPRINT_ID              = $blueprintId
    AGENT365_BLUEPRINT_CLIENT_SECRET   = $bpSecret
    AGENT365_AGENT_ID                  = $AgentId
    AGENT365_BLUEPRINT_SCOPE           = "api://$blueprintId/$ScopeValue"
    WEB_CLIENT_ID                      = $web.appId
    WEB_CLIENT_SECRET                  = $webSecret
    WEB_REDIRECT_URI                   = $redirect
    AUTH_ENABLED                       = "true"
    ENABLE_A365_OBSERVABILITY_EXPORTER = "true"
}
if ($WhatIf) { $values.Keys | Sort-Object | ForEach-Object { Info "[WhatIf] $_=$(if ($_ -match 'SECRET') { '***' } else { $values[$_] })" } }
else { Set-DotEnv $values; Info "Done. Secrets are in .env only (git-ignored)." }

if (-not $WhatIf -and -not $otelGranted) {
    Write-Host "`nIncomplete: the OtelWrite grant above failed. Fix it before expecting data in Purview/Defender." -ForegroundColor Red
    Write-Host "Verify with: .\scripts\Test-Readiness.ps1" -ForegroundColor Yellow
    exit 1
}
if (-not $WhatIf -and -not $inheritOk) {
    Write-Host "`nIncomplete: a blueprint inheritable-permissions step failed (see above)." -ForegroundColor Red
    exit 1
}

Write-Host "`nNext: .\scripts\run.ps1  then sign in at http://localhost:8000" -ForegroundColor Green
Write-Host "Then: .\scripts\Test-Readiness.ps1  to check local configuration (not proof of delivery)." -ForegroundColor Green
