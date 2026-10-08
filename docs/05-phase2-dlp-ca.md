# 05 – Purview SDK + DLP (built) and Conditional Access (planned)

> 📊 Diagrams for this walkthrough: [00-diagrams.html](00-diagrams.html) (open in a browser)

Section A is implemented. Section B (Conditional Access) is still a design.

The Purview SDK (Microsoft Graph Purview APIs) is **built into both the registration and the agent**:

- **Registration:** `Configure-Entra.ps1` (step 4 in [02-register.md](02-register.md)) adds the Graph Purview
  scopes to the blueprint as inheritable permissions and grants tenant-wide consent, so every agent identity
  inherits them. `Test-Readiness.ps1 -Tenant` checks them.
- **Runtime:** every chat turn calls `processContent` for the prompt (before LangGraph runs the model) and for the
  response (before it's shown). This is separate from OpenTelemetry, which carries activity telemetry only and
  doesn't apply prompt/response policies.

## A. Purview SDK and DLP: San Francisco allowed, Dallas blocked

**Demo:** Alice asks *"weather in San Francisco"* → answer. Alice asks *"weather in Dallas"* → *"Blocked by your
organization's data protection policy (Microsoft Purview)."* Nothing about Dallas is in the agent code; it's a Purview policy.

### What the agent does on every turn (`agent/purview.py`, `agent/guard.py`)

| Step | Graph call (v1.0, `/me/dataSecurityAndGovernance/...`) | Permission (inherited from the blueprint) |
|---|---|---|
| 1. Which policies cover this user + agent? Cached per user with its ETag, refreshed after 60 min or when Purview says `modified` | `POST protectionScopes/compute` (`uploadText,downloadText`, location = agent app ID) | `ProtectionScopes.Compute.User` |
| 2. Prompt, **before the LLM** (`uploadText`) | `POST processContent` with the prompt **text** + `aiAgentInfo` (agent ID, blueprint ID) | `Content.Process.User` |
| 3. Response, **before it's shown** (`downloadText`) | `POST processContent` with the response text | `Content.Process.User` |
| 4. No policy scopes the user | `POST activities/contentActivities` (metadata only, no text) | `ContentActivity.Write` |

- `executionMode = evaluateInline` → the turn waits for `processContent`; `restrictAccess` + `block` stops it
  (a blocked prompt never reaches the LLM). `evaluateOffline` → sent in the background, never blocks.
- Token: blueprint T1 + the user's token → **agent identity OBO** for
  `https://graph.microsoft.com/{ProtectionScopes.Compute.User,Content.Process.User,ContentActivity.Write}`.
  These scopes are **inheritable permissions** on the blueprint (set by `Configure-Entra.ps1`, step 6b).
- `processContent` is what sends prompt/response **text** to Purview. OpenTelemetry and `contentActivities` send
  metadata only. That's why Activity explorer didn't show the prompt before this was built.
- The chat UI shows the result per turn, for example `purview: prompt:inline-allowed · response:inline-allowed`.
  `GET /api/purview/scopes` (signed in) shows the user's raw protection scopes.

| `.env` setting | Default | Meaning |
|---|---|---|
| `PURVIEW_ENABLED` | `true` | Call the Purview SDK / Graph Purview APIs (only when `AUTH_ENABLED=true`) |
| `PURVIEW_APP_LOCATION_ID` | agent identity ID | `applicationLocation` sent to Purview. **Must equal the DLP policy location.** |
| `PURVIEW_LOG_WHEN_UNSCOPED` | `true` | Send `contentActivities` when no policy applies |
| `PURVIEW_FAIL_CLOSED` | `false` | `true` blocks the turn if an inline check fails (production); `false` fails open (demo) |

### Set up the Dallas policy

1. Prerequisites: Purview pay-as-you-go billing linked, DSPM for AI turned on, auditing on.
   Install `ExchangeOnlineManagement` with `Install-Module ExchangeOnlineManagement -Scope CurrentUser`.
2. **Custom sensitive information type** `Demo - Restricted City` with keyword `Dallas`: Purview portal →
   Information protection → Classifiers → Sensitive info types → Create (keyword list, case-insensitive). Wait ~15 min.
3. **DLP policy for the agent.** Entra-registered apps and agents can only be targeted from Security & Compliance
   PowerShell, not the portal UI:

   ```powershell
   .\scripts\New-PurviewDlpDemo.ps1 -WhatIf                                   # preview
   .\scripts\New-PurviewDlpDemo.ps1 -UserPrincipalName admin@contoso.onmicrosoft.com
   ```

   This creates a policy with location `Applications` / `Entra` = the agent identity app ID, `-EnforcementPlanes Application`,
   and a rule with `-RestrictAccess UploadText=Block, DownloadText=Block` when the SIT matches.
   **Scope warning:** the script includes all `.env.agent*` instances, not only the base agent.
   Supplying `-AppId` does not exclude overlays. For the Agent1-blocked/Agent2-unblocked comparison,
   review and change policy targeting first; do not apply the current script unchanged.
4. Wait for sync (up to ~1 hour). Sign in, open `http://localhost:8000/api/purview/scopes`, and check that
   `modes.uploadText` = `evaluateInline`. If it stays `evaluateOffline` or `none`, the policy isn't applied to this
   app ID or user yet.
5. To see prompts and responses in Activity explorer even without DLP, add a DSPM for AI **collection policy** that
   captures content for this agent/app. Without any policy, only metadata-only `contentActivities` are logged.

### Show it

1. *weather in San Francisco* → answer; meta line shows `purview: prompt:inline-allowed · response:inline-allowed`.
2. *weather in Dallas* → red "Blocked by your organization's data protection policy (Microsoft Purview)."; the LLM
   and tools never ran (check Jaeger: no `Chat`/`execute_tool` spans under that `invoke_agent`).
3. Purview → DSPM for AI → **Activity explorer**: the prompt/response text and the DLP rule match
   (can take 30–60 min). Purview → DLP **Alerts** → the alert; Defender XDR → Incidents & alerts.

### Troubleshooting

| Symptom | Cause / fix |
|---|---|
| `purview: token-error: ... AADSTS65001` | Inherited Graph scopes not consented. Rerun `Configure-Entra.ps1` (step 6b), then `Test-Readiness.ps1 -Tenant` |
| `purview: prompt:error: ... HTTP 403` | Token lacks the scope, or Purview billing / DSPM for AI isn't set up for the tenant |
| Always `no-scope-activity-logged` | No policy covers this user and app ID. Check that `PURVIEW_APP_LOCATION_ID` equals the DLP policy location |
| Always `offline-sent`, never blocks | The DLP policy was created in the portal (not `New-DlpComplianceRule`) or hasn't synced yet |
| Dallas not blocked but scopes are inline | SIT keyword not matching yet (wait for replication) or the rule lacks `RestrictAccess ... Block` |

## B. Conditional Access: Alice allowed, Bob blocked

**Demo:** Alice (compliant or Entra‑joined device, or on the corporate network) signs in and uses the agent. Bob on an
unmanaged device, or off‑network, gets an AADSTS53000/53003 block at sign‑in, so he never reaches the agent.

Design:

1. **Named location** `Corp network`: the office or VM public egress IP(s), marked as trusted.
2. **CA policy** `Demo – Agent requires managed device or corp network`:
   - Users: a group with Alice and Bob (exclude break‑glass accounts)
   - Target resources: `Laszlo-AgentRegistryDemo1-WebClient` **and** the blueprint resource (`api://<blueprint>`)
   - Conditions: any location, **excluding** `Corp network`
   - Grant: **Require compliant device** (or hybrid‑joined)
   - Start in **report‑only**, then switch to **On** for the demo
3. Alice's laptop is Intune‑enrolled and compliant; Bob uses an unmanaged VM (or a phone hotspot to be off‑network).
4. Show: Bob's error page, then Entra sign‑in logs → *Conditional Access* tab → the policy result. Use the **What If** tool for both users.
5. Optional: CA for **agent identities** (Entra Agent ID CA, preview) to block the agent itself based on risk.

Open questions for Phase 2 (from the design interview): the exact corporate IP ranges; whether Bob's device is a VM or
a personal machine; whether to show CA on the agent identity in addition to the user.
