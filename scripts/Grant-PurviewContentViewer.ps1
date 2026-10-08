<#
.SYNOPSIS
    Grant a user permission to SEE prompt and response text in Purview Activity explorer.

.DESCRIPTION
    Purview deliberately separates "see that an AI interaction happened" from "see what was said".
    Global Administrator grants the first but NOT the second, so a GA sees AI Interaction rows with
    agent name, tool calls and timestamps, yet the prompt and answer stay hidden.

    Microsoft's permission table marks this row as NOT supported for Compliance Administrator,
    Global Administrator, the Purview Compliance Administrator role group and every view-only role:

        "View the prompts and responses within AI Interaction events from activity explorer"
            -> requires: Data Security AI Content Viewers   (most targeted - AI prompts only)
                     or: Content Explorer Content Viewer    (broader - all content explorer data)

    https://learn.microsoft.com/purview/data-security-posture-management-permissions

    These are MICROSOFT PURVIEW role groups, assigned in the Purview portal. They are NOT Entra
    roles, so you cannot get them by elevating through PIM. In particular, the Entra role
    'Purview Workload Content Administrator' does NOT grant this: it is managed by the Purview
    Role Assignment Migrator (it syncs OUT of Purview), covers SharePoint/Teams/OneDrive/Exchange
    content operations, and Microsoft explicitly says not to assign it directly in Entra.

    This script adds a user to a role group in Security & Compliance PowerShell.

    Sign-in is interactive by design: Security & Compliance PowerShell does not accept an
    Azure CLI token, so this cannot reuse the `az login` session the way Configure-Entra.ps1 does.

.PARAMETER UserPrincipalName
    The user to grant. Defaults to the signed-in `az` account.

.PARAMETER RoleGroup
    Role group to add the user to. Defaults to 'Data Security AI Content Viewers', which grants
    exactly the AI prompt/response visibility and nothing else. If that group doesn't exist in
    the tenant the script falls back to reporting the available candidates.

.PARAMETER WhatIf
    Show what would change without changing it.

.PARAMETER CheckOnly
    Read-only. Report whether the user is already a member of either role that can reveal
    prompt text, and change nothing. Use this to answer "has this already been done?".

.EXAMPLE
    .\scripts\Grant-PurviewContentViewer.ps1 -CheckOnly
    .\scripts\Grant-PurviewContentViewer.ps1
    .\scripts\Grant-PurviewContentViewer.ps1 -UserPrincipalName alice@contoso.onmicrosoft.com

.NOTES
    Run from a normal interactive terminal window. WAM sign-in needs a real console window handle;
    in an embedded or background terminal it fails with "A window handle must be configured". The
    script retries with -DisableWAM, but a fully headless host has no interactive sign-in at all --
    use the Purview portal there:
      Settings > Roles and scopes > Role groups > Content Explorer Content Viewer > Edit
#>
[CmdletBinding()]
param(
    [string]$UserPrincipalName,
    [string]$RoleGroup = "Data Security AI Content Viewers",
    [switch]$WhatIf,
    [switch]$CheckOnly
)

$ErrorActionPreference = "Stop"

function Step($m) { Write-Host "==> $m" -ForegroundColor Cyan }
function Info($m) { Write-Host "    $m" }
function Warn($m) { Write-Host "    $m" -ForegroundColor Yellow }

if (-not $UserPrincipalName) {
    Step "No -UserPrincipalName given; reading the signed-in az account"
    $acct = az account show 2>$null | ConvertFrom-Json
    if (-not $acct) { throw "Not signed in to az and no -UserPrincipalName given." }
    $UserPrincipalName = $acct.user.name
    Info $UserPrincipalName
}

if (-not (Get-Module -ListAvailable ExchangeOnlineManagement)) {
    throw "ExchangeOnlineManagement is not installed. Run: Install-Module ExchangeOnlineManagement -Scope CurrentUser"
}

Step "Connecting to Security & Compliance PowerShell (interactive sign-in)"
Warn "Sign in as a Global Administrator. The browser window may open behind this one."
Import-Module ExchangeOnlineManagement -ErrorAction Stop

$connectArgs = @{ ShowBanner = $false }
if ($UserPrincipalName) { $connectArgs.UserPrincipalName = $UserPrincipalName }
try {
    Connect-IPPSSession @connectArgs
}
catch {
    # WAM (Web Account Manager) needs a real console window handle. In embedded terminals and
    # non-interactive hosts it fails with "A window handle must be configured"; fall back to the
    # plain browser flow, which has no such requirement.
    if ($_.Exception.Message -match "window handle") {
        Warn "WAM is unavailable in this terminal; retrying with -DisableWAM."
        Connect-IPPSSession @connectArgs -DisableWAM
    }
    else { throw }
}

try {
    if ($CheckOnly) {
        # Report membership of BOTH roles that can reveal prompt text, so you can answer
        # "has this already been done?" without changing anything.
        Step "Read-only check for $UserPrincipalName"
        $candidates = @(
            "Data Security AI Content Viewers",
            "Content Explorer Content Viewer",
            "Microsoft Purview Data Security AI Content Viewer"
        )
        $found = $false
        foreach ($name in $candidates) {
            $g = Get-RoleGroup -Identity $name -ErrorAction SilentlyContinue
            if (-not $g) { Info "$name : not present in this tenant"; continue }
            $m = @(Get-RoleGroupMember -Identity $name -ErrorAction SilentlyContinue) | Where-Object {
                $_.WindowsLiveID -eq $UserPrincipalName -or
                $_.PrimarySmtpAddress -eq $UserPrincipalName -or
                $_.Name -eq $UserPrincipalName
            }
            if ($m) { Write-Host "    [ OK ] MEMBER of '$name'" -ForegroundColor Green; $found = $true }
            else { Warn "[    ] not a member of '$name'" }
        }
        Write-Host ""
        if ($found) {
            Write-Host "  Prompt text SHOULD be visible. If it isn't, sign out of the Purview" -ForegroundColor Yellow
            Write-Host "  portal completely and sign back in, and make sure you are opening an" -ForegroundColor Yellow
            Write-Host "  invoke-agent row rather than an 'Execute Tool by SDK' row." -ForegroundColor Yellow
        }
        else {
            Write-Host "  Prompt text will be HIDDEN. Global Administrator does NOT grant it." -ForegroundColor Red
            Write-Host "  Run this script without -CheckOnly, or use the Purview portal." -ForegroundColor Red
        }
        Write-Host ""
        return
    }

    Step "Checking role group '$RoleGroup'"
    $rg = Get-RoleGroup -Identity $RoleGroup -ErrorAction SilentlyContinue
    if (-not $rg) {
        Write-Host ""
        Write-Host "  Role group '$RoleGroup' not found in this tenant." -ForegroundColor Red
        Write-Host "  Available role groups matching 'Content Explorer' / 'AI' / 'Data Security':" -ForegroundColor Yellow
        Get-RoleGroup | Where-Object { $_.Name -match "Content Explorer|AI|Data Security" } |
            ForEach-Object { Write-Host "    - $($_.Name)" -ForegroundColor Yellow }
        throw "Role group '$RoleGroup' not found."
    }

    $members = @(Get-RoleGroupMember -Identity $RoleGroup -ErrorAction SilentlyContinue)
    $already = $members | Where-Object {
        $_.WindowsLiveID -eq $UserPrincipalName -or
        $_.PrimarySmtpAddress -eq $UserPrincipalName -or
        $_.Name -eq $UserPrincipalName
    }

    if ($already) {
        Info "$UserPrincipalName is ALREADY a member of '$RoleGroup'."
        Info "If prompts are still hidden, sign out of the Purview portal completely and sign back in."
    }
    elseif ($WhatIf) {
        Info "[WhatIf] Add-RoleGroupMember -Identity '$RoleGroup' -Member '$UserPrincipalName'"
    }
    else {
        Step "Adding $UserPrincipalName to '$RoleGroup'"
        Add-RoleGroupMember -Identity $RoleGroup -Member $UserPrincipalName -Confirm:$false
        Info "Added."
    }

    Write-Host ""
    Write-Host "  Role group membership can take 15-30 minutes to take effect, and you must" -ForegroundColor Yellow
    Write-Host "  SIGN OUT of the Purview portal and sign back in for the new role to load." -ForegroundColor Yellow
    Write-Host ""
    Write-Host "  Then: purview.microsoft.com > DSPM > Discover > Activity explorer > AI activities" -ForegroundColor Yellow
    Write-Host "  Open a row whose Activity is NOT 'Execute Tool by SDK' - tool spans never carry" -ForegroundColor Yellow
    Write-Host "  prompt text. Look for the invoke-agent row and its 'Prompt'/'Response' fields." -ForegroundColor Yellow
    Write-Host ""
}
finally {
    Disconnect-ExchangeOnline -Confirm:$false -ErrorAction SilentlyContinue | Out-Null
}
