# Registration: a365 CLI, Entra wiring, registry

Customer-facing walkthrough: `docs/02-register.md` in the template. This file is the operator's cheat sheet.

## Prerequisites

- .NET 8+ and `dotnet tool install -g Microsoft.Agents.A365.DevTools.Cli` (validated with 1.1.174).
- `az login --tenant <domain> --allow-no-subscriptions`. Demo tenants often have no Azure subscription.
- Global Admin, or Application Admin plus Cloud App Admin, for consent. Admin consent for inheritable permissions needs Global Admin
  or Privileged Role Admin.
- At least one user with an **assigned** E7 or Agent 365 license.
- Frontier or preview enrollment as required by the tenant. `a365 setup requirements` warns about it. Azure hosting checks can be ignored for a local agent.

## Sequence

```powershell
a365 setup requirements
a365 config init                                    # writes a365.config.json (tenantId, clientAppId, display names)
a365 setup all --agent-name <Name> --authmode obo --dry-run
a365 setup all --agent-name <Name> --authmode obo   # may show a WAM sign-in window
.\scripts\Configure-Entra.ps1 -UseAzCli -WhatIf     # preview
.\scripts\Configure-Entra.ps1 -UseAzCli             # web client, consent, inheritable perms, .env
.\scripts\Test-Readiness.ps1 -Tenant
```

`a365 setup all` creates the following and writes `a365.generated.config.json`:
- Blueprint, blueprint SP, `access_agent_as_user` scope and secret.
- Agent identity.
- Registry entry.
- OtelWrite grant.

The generated file contains `agentBlueprintId`, `agenticAppId`, `agentRegistrationId` and `agentBlueprintClientSecret`.
The `…Protected` variant is DPAPI-encrypted and can't be read from PowerShell. `completed: false` can persist even after a successful
run. Check `%LOCALAPPDATA%\Microsoft.Agents.A365.DevTools.Cli\logs\a365.setup.log` for "Setup completed successfully".
It logs "Skipping Federated Identity Credential creation (no MSI…)" for local agents, which is expected.

Never commit these files: `.env`, `a365.config.json` and `a365.generated.config.json`. The template `.gitignore` covers them.

## Inheritable permissions (agent identities inherit from the blueprint)

```powershell
a365 setup permissions custom --resource-app-id 00000003-0000-0000-c000-000000000000 `
  --scopes "Content.Process.User,ContentActivity.Write,ProtectionScopes.Compute.User"     # Purview SDK
a365 setup permissions custom --resource-app-id 9b975845-388f-4429-889e-eab1ef63949c --scopes "Agent365.Observability.OtelWrite"
a365 setup permissions custom --resource-app-id 8578e004-a5c6-46e7-913e-12f58912df43 --scopes "Connectivity.Connections.Read"  # optional, Power Platform
```

Each command adds the required resource access, an AllPrincipals grant from the blueprint SP and the inheritable scopes, then verifies them.
It's idempotent and resolves `a365.config.json` from the current directory. `Configure-Entra.ps1` loops `$InheritablePermissions`.
In the Entra portal, the agent identity shows the permissions as "Inherited from parent".
To read them back: `GET https://graph.microsoft.com/beta/applications/{bpObjectId}/microsoft.graph.agentIdentityBlueprint/inheritablePermissions`.

**Portal gotcha:** inheritance is resolved at token time. The agent identity's Enterprise application → Permissions blade lists only
explicit `oauth2PermissionGrants` and app role assignments, so it can look empty, or show only the admin's `Principal` grants from
`--authmode obo`, while tokens still work. Customers expect it to match the blueprint, so run `scripts\Sync-AgentPermissions.ps1`.
It copies each blueprint `AllPrincipals` grant, filtered to its inheritable scopes, and each inheritable app role assignment
(observability `OtelWrite` role `8f71190c-…`) to every agent ID in `.env`/`.env.agent*`. It's idempotent and uses the az token,
which has `DelegatedPermissionGrant.ReadWrite.All` and `AppRoleAssignment.ReadWrite.All`. `Configure-Entra.ps1` and
`New-AgentInstance.ps1` call it.

**Every `a365` invocation** (even `--version`) queries `api.nuget.org/v3-flatcontainer/microsoft.agents.a365.devtools.cli` for updates.
On corp devices with Defender network protection, that pops a "blocked" toast each time. It's harmless, but prefer Graph for read-only checks.

## More agent instances on the same blueprint

The CLI creates only the first agent identity. `scripts\New-AgentInstance.ps1 -Name "<X> Agent" -Port 8001` adds more, idempotently:

1. Identity: get a blueprint client-credentials token (`client_id`=blueprint, the blueprint secret, scope Graph `.default`), then
   `POST /beta/serviceprincipals/Microsoft.Graph.AgentIdentity` with header `OData-Version: 4.0` and body
   `{displayName, agentIdentityBlueprintId, "sponsors@odata.bind": [user], "owners@odata.bind": [user]}`. Admin tokens can list
   identities (`/beta/servicePrincipals/microsoft.graph.agentIdentity?$filter=agentIdentityBlueprintId eq '<bp>'`) but creation uses the blueprint token.
2. Add `http://localhost:<port>/auth/callback` to the web client's `web.redirectUris` (PATCH, az token).
3. Registry: `POST /beta/copilot/agentRegistrations` with required `displayName, createdBy, sourceCreatedDateTime, sourceLastModifiedDateTime`
   plus `ownerIds, agentIdentityId, agentIdentityBlueprintId, sourceAgentId`. It needs delegated `AgentRegistration.ReadWrite.All`, which az lacks;
   `scripts\agent_registry.py` uses MSAL loopback on the a365 CLI client app, which avoids the WAM window-handle problem.
   The record ID equals the agent identity ID. GET-by-id works, but collection GET returns 404.
4. Write a `.env.agent<N>` overlay (APP_PORT, WEB_REDIRECT_URI, AGENT365_AGENT_ID/NAME, AGENT365_REGISTRATION_ID). `config.py` loads
   `AGENT_ENV_FILE` before `.env`, and `run.ps1 -EnvFile` sets it. Name the session cookie per port; localhost cookies are shared across ports.
5. Permissions are inherited, so there's no new consent. `New-AgentInstance.ps1` also runs `Sync-AgentPermissions.ps1` so the portal shows them. Verify with T1 (`fmi_path`=new ID), then an agent app token for the observability API; it should carry the `OtelWrite` role.
6. `New-PurviewDlpDemo.ps1` sets the policy `-Locations` to all instances (Set-DlpCompliancePolicy replaces the set).

## Why Configure-Entra exists

- The `--authmode obo` grants are `consentType=Principal`, covering only the admin. Other users need an AllPrincipals grant or inheritance.
- The CLI doesn't create a browser-capable client. Blueprints and agent identities can't do `/authorize`.
- It writes the real IDs to `.env`, so spans carry the real `gen_ai.agent.id` and `microsoft.tenant.id` instead of the
  `local-dev-agent` placeholders.
- `-IdsOnly` copies IDs without making Graph calls. `-UseAzCli` reuses the az token, whose `scp` includes Application.ReadWrite.All,
  DelegatedPermissionGrant.ReadWrite.All and AppRoleAssignment.ReadWrite.All.

## Verify the registry (read-only)

- `GET /beta/copilot/agentRegistrations/{agentRegistrationId}` needs AgentRegistration.Read(Write).All. That scope isn't in the az token,
  so use `Connect-MgGraph -ClientId <a365 clientAppId> -Scopes AgentRegistration.ReadWrite.All`. The legacy `/beta/agentRegistry/agentInstances` returns 404.
- `GET /beta/servicePrincipals/{agentId}/microsoft.graph.agentIdentity` returns `agentIdentityBlueprintId`, sponsors and owners.
- M365 admin center → Agents → All agents shows the agent. Entra → Agent ID → Agent identities shows the identity.

## Graph and PowerShell pitfalls

- `oauth2PermissionGrants` can be filtered only by `clientId`, `resourceId` or `principalId`.
- `servicePrincipals?$filter=appId eq '<non-guid>'` returns HTTP 400. Never pass placeholders, especially in `-WhatIf` paths.
- PowerShell variable names are case-insensitive (`$g` vs `$G`). Use names like `$GraphBase`.
- `az rest` on Windows mangles `?` and `(`. Use `az account get-access-token --resource-type ms-graph` and then `Invoke-RestMethod`.
- `Connect-MgGraph` and `Connect-IPPSSession` WAM prompts fail in embedded or background terminals ("window handle must be configured").
  Run them in a real console window, or open the terminal canvas for the user.

## Cleanup

`.\scripts\reset.ps1` resets local state. `.\scripts\reset.ps1 -Tenant` also runs `a365 cleanup --agent-name <Name>` and deletes the
`<Name>-WebClient` app via Connect-MgGraph. Deletion needs explicit user approval.
