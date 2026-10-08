# Admin lab: reproduce the demo in your own tenant

Use a **dedicated test tenant**, not production. Run PowerShell 7 from the cloned project root.
Names beginning `Laszlo-` are sample display names, not a dependency on the original tenant.
Replace all `<...>` placeholders with values from your own tenant. No existing tenant credentials
or registration output are distributed with this project.

## What is implemented

Local weather/geocode tools, Entra OBO sign-in, registry integration, Agent 365 telemetry,
Purview prompt/response checks, DLP setup, additional registered instances and Diagnostics are implemented.
**Conditional Access is a manual design, not an automated or verified deployment.**
The three-agent slide describes an intended comparison: the local-only third instance has not
been provisioned, and the current DLP script targets all configured registered instances.

## Prerequisites and impact

Use Windows with Python 3.12, PowerShell 7, Git and winget; allow disk space for the local model.
Setup downloads Python dependencies, Ollama/model and Jaeger. Tools need internet even when the model is local.
Tenant setup requires Azure CLI, .NET/Agent 365 CLI and Microsoft.Graph.Authentication:
see [02-register.md](02-register.md) for commands and current tenant prerequisites.

Arrange the relevant Entra admin/consent permissions, an assigned Agent 365/E7 license for telemetry,
Defender hunting access, Purview audit/DSPM for AI and billing prerequisites for DLP.
Install the policy-management module before the DLP or content-viewer steps:

```powershell
Install-Module ExchangeOnlineManagement -Scope CurrentUser
```

Registration creates identities, credentials, a browser client and tenant-wide consent.
The DLP script enables an enforcement policy. Content-viewer setup grants access to sensitive content.
CA can lock users out: exclude emergency accounts, use a dedicated test group, and begin report-only.
Review each change and its preview before applying it. Do not expose this localhost demo on the internet.
Licensing, preview availability and admin portals may vary; confirm current requirements in the linked
Microsoft documentation before incurring charges or changing policies.

## Run in this order

| Stage | Action | Evidence required before continuing |
|---|---|---|
| 1. Local baseline | Follow [01-run-agent.md](01-run-agent.md): `.\scripts\setup.ps1`, then `.\scripts\run.ps1` | San Francisco weather and Redmond geocode return results; Jaeger shows a root `invoke_agent` with inference/tool children |
| 2. Identity and registry | Follow [02-register.md](02-register.md): tenant login, requirements, dry run and registration, then `Configure-Entra.ps1 -UseAzCli -WhatIf` and the real run | Blueprint, agent identity and registry entry exist; browser sign-in works |
| 3. Permissions | `.\scripts\Test-Readiness.ps1 -Tenant`; review `Sync-AgentPermissions.ps1` from the registration guide | Purview scopes and OtelWrite are consented/inheritable; explicit grant mirror matches the blueprint if using the Entra Permissions blade |
| 4. Telemetry | Follow [03-observability.md](03-observability.md); enable `A365_VERBOSE=true`, restart and sign in again | Export response/routing and matching records in Defender/Purview/M365; Jaeger alone is not cloud-delivery proof |
| 5. Purview DLP | Follow section A of [05-phase2-dlp-ca.md](05-phase2-dlp-ca.md): billing/DSPM, custom Dallas SIT, policy preview, then apply | Signed-in `/api/purview/scopes` shows inline evaluation; SF succeeds; Dallas is blocked before the model/tools run |
| 6. Content access | Follow the content-viewer section in [03-observability.md](03-observability.md) | Authorized viewer can read prompt/response in AI Activity explorer; a Global Admin role alone is not proof of content access |
| 7. Second identity | Follow “More agent instances” in [02-register.md](02-register.md); start the new overlay on :8001 | Distinct agent ID/registry record, sign-in and both SDK tokens; telemetry identifies the correct instance |
| 8. Optional CA | Manually follow section B of [05-phase2-dlp-ca.md](05-phase2-dlp-ca.md) | Report-only/What If checked, then real allow/block sign-in logs after explicitly enabling the test policy |

Allow service propagation time; pre-stage activity an hour before presenting. Restarting the app clears
sessions: sign in again. SDK configuration or a passing local readiness check does not prove cloud ingestion.
Purview fail-open is the demo default: an API error is **not** an allow verdict. Inspect the per-turn status;
use `PURVIEW_FAIL_CLOSED=true` when validating that inline-check failures block.

## Diagnostics and privacy

Diagnostics shows decoded token claims/fingerprints, not raw token strings.
With `DIAG_ADMIN_GRAPH=true`, it uses the operator's local Azure CLI session to read directory/CA information;
any signed-in demo user can see that information. Keep the service local, use synthetic prompts and
`DIAG_ADMIN_GRAPH=false` when directory details are not needed.
Prompts, responses and tool arguments can be recorded locally and/or sent to Microsoft services.
Do not use customer confidential information or publish screenshots, traces or HTTP archives without review.

## Three-agent comparison: preparation is still required

The intended talk track is **Agent3 (local) → Agent2 (observable) → Agent1 (governed)**.
Agent1/2 retain Entra, observability and the Purview SDK; only Agent1 should be targeted by the Dallas-block rule.
Agent3 needs a separate local-only overlay/port with auth, cloud export and Purview disabled.
Do not run `New-PurviewDlpDemo.ps1` unchanged for the contrasting-policy story:
it includes every `.env.agent*` instance, even when `-AppId` is supplied.
First change/review policy targeting or manage it explicitly in Security & Compliance PowerShell.
Verify all three with the same prompts and check no other blocking policy covers Agent2.
See [presenter-script.md](presenter-script.md) and diagram 11 for the matrix and expected results.

## Reset and cleanup limits

Follow [04-reset.md](04-reset.md), but do not assume cleanup is comprehensive:
the current reset script stops ports 8000/16686, not additional instances on 8001/8002.
Stop additional listeners by their verified PID. Tenant reset uses CLI cleanup and removes the browser client;
inspect remaining additional identities/registry entries manually.
Remove test DLP policies/rules, sensitive info types, content-viewer role memberships and CA policies
separately, and review any billing resources created for the lab.
Ignored overlays and generated configs can still contain secrets/stale IDs after cleanup; never reuse them blindly.
Cloud audit/telemetry records follow service retention and are not erased by local reset.

## Before publishing your fork

```powershell
python .\scripts\Test-PublishReadiness.py
```

This checks ignore rules and publication candidates without contacting the tenant.
It compares candidates with your local credential/identity values and scans for token/private-key patterns;
it prints paths and categories, never matched secret values.
It is a heuristic, not a guarantee: review all staged files, document metadata and any images manually.
Use Git to upload **only reviewed tracked files**, not an archive of this working folder.
`.gitignore` does not protect already tracked files, existing Git history or a ZIP upload.
If a credential has been published, revoke/rotate it before addressing Git history.
Operator-specific `docs/planning/` notes remain local and ignored; this admin guide replaces them for public use.
Do not publish until you have chosen appropriate licensing/redistribution terms for your code and bundled material.

## Optional presentation rebuild

The saved PPTX is ready to view; rebuilding is not needed to run the lab.
For a rebuild, install the separate authoring tools (not part of runtime setup):
Node.js with `npm install -g pptxgenjs`, and
`.\.venv\Scripts\python.exe -m pip install playwright`. Microsoft Edge must be installed.
Run `.\docs\deck\Build-Deck.ps1`; add `-Render` only if desktop PowerPoint is installed.
The HTML diagrams load Mermaid from a public CDN, so the preview/export needs internet access.
