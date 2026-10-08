---
name: agent365-third-party-agent
description: Build, register and demo a new third-party (non-Microsoft) AI agent with Microsoft Agent 365 on a local machine or VM. Scaffolds a working LangGraph + FastAPI agent from a proven template, then registers it in Entra Agent ID with the a365 CLI (blueprint, agent identity, OBO, inheritable permissions). Covers Agent 365 OpenTelemetry to Defender and Purview, Purview SDK (processContent) DLP blocking, Conditional Access for users and agents, a diagnostics tab, and customer walkthrough docs. Use whenever the user wants to create or clone an Agent 365 demo agent, bring a custom, ISV or LangGraph agent under Agent 365, use the a365 CLI, agent registry, blueprint or agent identity, wire microsoft-opentelemetry or the Agent 365 observability SDK, show agent activity in Purview or Defender, demo DLP or Conditional Access against an agent, or debug missing Agent 365 telemetry or Purview interactions, even if they don't say "skill".
---

# Agent 365 third-party agent: build, register, observe, govern

This skill packages a working, customer-tested implementation, built and debugged over many sessions.
Reuse the template and follow the phases in order. Most of the expensive failures in this space were
silent: telemetry that never left the machine, scopes Purview ignored, and roles that hid content.
The phases below are built to surface those failures early.

## What you get

`assets/template/` is a complete, sanitized project. `scripts/new_agent.py` stamps it out with a new name:

| Path | Purpose |
|---|---|
| `agent/app.py` | FastAPI chat UI and API. Each turn: Purview prompt guard → LangGraph → response guard, wrapped in `InvokeAgentScope` |
| `agent/graph.py`, `agent/tools.py` | LangGraph ReAct agent and its tools. **These are the parts you replace for a new agent.** |
| `agent/auth.py` | MSAL sign-in, then blueprint T1 (`fmi_path`), then agent-identity OBO tokens (observability and Purview) |
| `agent/telemetry.py`, `agent/a365_callbacks.py` | `microsoft-opentelemetry` distro, Agent 365 exporter, Jaeger, and manual Inference/Tool scopes |
| `agent/purview.py`, `agent/guard.py` | Graph Purview APIs: protectionScopes/compute, processContent, contentActivities |
| `agent/diagnostics.py`, `web/index.html` | Chat and Diagnostics tabs (token claims, identity, grants, CA policies, Purview scopes) |
| `scripts/setup.ps1`, `scripts/run.ps1` | Install Python 3.12, Ollama, Jaeger and `.venv`; start everything |
| `scripts/Configure-Entra.ps1` | Post-`a365 setup` wiring: web client app, consent, inheritable permissions, `.env` |
| `scripts/Sync-AgentPermissions.ps1` | Mirror blueprint grants and app roles onto every agent identity so the Entra Permissions blade matches the blueprint |
| `scripts/Test-Readiness.ps1` | Read-only check that config is real, not placeholder (`-Tenant` also checks scopes) |
| `scripts/New-PurviewDlpDemo.ps1`, `scripts/Grant-PurviewContentViewer.ps1` | DLP policy for the agent; role needed to read prompts |
| `scripts/reset.ps1` | Local reset; `-Tenant` for full cleanup |
| `tests/` | Offline tests: span shape, token lifetime, Purview decisions, diagnostics |
| `docs/` | Customer walkthrough: run → register → observability → reset → DLP/CA, presenter script, diagrams |

## Workflow

Track these as todos. Each phase ends with a checkpoint you must actually observe. "It should work" is not enough.

### Phase 0: Interview (short)

Ask only what changes the build. Use ask_user, one question at a time:
1. What does the agent do? Its tools must be simple, keyless public APIs or local logic, so the demo can't break on a key.
2. Agent name (letters, digits, hyphens), provider/ISV name, and target folder.
3. Tenant domain, and whether the presenter has Global Admin plus an **assigned** Microsoft 365 E7 or Agent 365 license.
   Without an assigned license the service returns 200 but drops all telemetry (`tenant_not_licensed`).
4. LLM: local Ollama (default `qwen2.5:7b`, no key) or GitHub Models (`GITHUB_TOKEN`).
5. Policy demo: which allowed vs blocked value to show for DLP (e.g. "San Francisco" allowed, "Dallas" blocked),
   and which users and devices to show for Conditional Access.

If the user said "grill me", invoke the grill-me skill instead and record decisions in `docs/planning/`.

### Phase 1: Scaffold and customize

```powershell
python <skill>\scripts\new_agent.py --dest D:\Repos\<Folder> --name <Agent-Name> --provider "<ISV>" --description "<one line>"
```
Then follow `references/customize.md`. In short:
- Rewrite `agent/tools.py` (keep `ALL_TOOLS` and `@tool` docstrings, which are what the LLM sees).
- Rewrite `SYSTEM_PROMPT` in `agent/graph.py`.
- Update the UI emoji and examples, and the domain wording in docs (search for weather, geocode, San Francisco, Dallas).
- Adjust the DLP keyword.

Don't touch auth, telemetry, Purview or diagnostics code unless you have a reason. It encodes the fixes listed under "Rules you must not relearn".

**Checkpoint 1:** run `.\scripts\setup.ps1` and `.\scripts\run.ps1`. Ask a domain question at http://localhost:8000. A trace with root `invoke_agent` and `chat` / `execute_tool` children appears in Jaeger (http://localhost:16686).
Run the tests with `$env:AUTH_ENABLED='false'; $env:ENABLE_A365_OBSERVABILITY_EXPORTER='true'; .\.venv\Scripts\python.exe -m unittest discover tests`.

### Phase 2: Register in Entra and Agent 365

Read `references/registration.md`. The sequence:
1. `az login --tenant <domain> --allow-no-subscriptions`.
2. `a365 setup requirements`.
3. `a365 config init` (writes `a365.config.json`).
4. `a365 setup all --agent-name <Name> --authmode obo --dry-run`, then for real.

The CLI creates the blueprint, agent identity, registry entry and OtelWrite grant.

Then run `.\scripts\dev\Test-ConfigureEntra.ps1`, an offline harness with a fake Graph that needs `a365.generated.config.json` from step 4.
After that, run `.\scripts\Configure-Entra.ps1 -UseAzCli`. It handles:
- The web client app the browser signs into (agent blueprints and agent identities can't do `/authorize`).
- Tenant-wide consent.
- **Inheritable blueprint permissions** (Graph Purview scopes, OtelWrite, Power Platform) via `a365 setup permissions custom`.
- Writing `.env` with `AUTH_ENABLED=true` and the exporter turned on.

**Checkpoint 2:** `.\scripts\Test-Readiness.ps1 -Tenant` reports all OK. The registry record exists (`GET /beta/copilot/agentRegistrations/{id}`). Restart the app, sign in, and check that the header shows the user.

If the user wants more agents on the same blueprint, run `.\scripts\New-AgentInstance.ps1 -Name "<X> Agent" -Port 8001`, then `.\scripts\run.ps1 -EnvFile .env.agent2`. The script is idempotent and covers the identity, redirect URI, registry record and overlay `.env`. Permissions are inherited, so the new agent needs no consent. See "More agent instances" in `references/registration.md`.

### Phase 3: Observability to Defender, Purview and MAC

Read `references/observability.md`. Telemetry goes to Jaeger and Agent 365 **independently**. A healthy Jaeger proves nothing about Microsoft 365.

**Checkpoint 3:** run with `A365_VERBOSE=true` and check the log shows `Exporting N spans` followed by a 2xx with sinks `sent`. Within 5 to 30 minutes, `CloudAppEvents` in Defender has `InvokeAgent`, `InferenceCall` and `ExecuteToolBySDK`. The agent also appears in Purview DSPM → Activity explorer → **AI activities**.

### Phase 4: Purview SDK and DLP (prompts and responses)

Read `references/purview-dlp.md`. OpenTelemetry is **telemetry only**. Policy evaluation of prompt and response text happens only through the Purview SDK. That means Graph `processContent`, called OBO as the agent identity, before the LLM (uploadText) and after it (downloadText).
Create the sensitive info type (SIT), then run `.\scripts\New-PurviewDlpDemo.ps1`. DLP for Entra-registered apps must be created in PowerShell. A policy built in the portal only evaluates offline and never blocks.

**Checkpoint 4:** the allowed prompt returns `purview: prompt:inline-allowed`. The blocked prompt returns the block message, and the DLP alert or activity appears in Purview.

### Phase 5: Conditional Access

Read `references/conditional-access.md`. There are two layers:
- **User sign-in CA** targets the web client and the blueprint resource. Use a named location plus a compliant-device rule. Alice is allowed; Bob gets AADSTS53000/53003.
- **Agent identity CA** targets the agent's own token requests, by agent ID or by a custom-security-attribute filter.

Start the policies in report-only, use What If, then turn them on.

**Checkpoint 5:** Bob's block page, plus the Entra sign-in log CA tab. The Diagnostics tab lists which policies target the user and which target the agent.

### Phase 6: Diagnostics, docs and handoff

Use the Diagnostics tab (`references/diagnostics.md`) to show the token chain and the applied policies live. Update `README.md` and `docs/*` for the new domain and regenerate the diagrams. Leave the presenter with `docs/presenter-script.md` and `docs/04-reset.md`.

Start the administrator at `docs/admin-validation.md`: it records deployment impact, clean-tenant
setup order, validation evidence and cleanup limitations. Do not describe passing configuration
checks as proof of cloud delivery. Conditional Access remains a manual design until actually tested.

**Three-agent comparison:** present Agent3 (local baseline) → Agent2 (registered/observable) →
Agent1 (governed). Agent1/2 share a blueprint and keep both SDKs enabled; target only Agent1 with
the Dallas-block rule. Agent3 uses a separate local-only overlay with auth, Agent 365 export and
Purview disabled. This is an intended setup, not something the template provisions automatically.
The current DLP script includes every `.env.agent*` instance, even with `-AppId`; review/change
policy targeting before using the contrasting-policy story. A collection policy can capture
Agent2 content without applying the Dallas-block rule.

**Presentation:** HTML diagram 11 and the presenter script contain the comparison.
`docs/deck/Build-Deck.ps1` builds `docs/Agent365-Demo-Diagrams-clean.pptx` with a light background,
generic DemoAgent names (including embedded diagram images) and generic author metadata.
It needs Node/global pptxgenjs, Python playwright and Edge; `-Render` additionally needs PowerPoint.
The template includes source/build scripts, not a prebuilt deck. Keep an operator-specific deck
locally, Git-ignored; never overwrite it with the clean build.

**Publication:** run `python scripts/Test-PublishReadiness.py`, review the staged files and choose
licensing terms. Ignore credentials/configs, logs/caches, private keys, Office locks, renders,
operator planning notes and private decks. Gitignore does not protect ZIP uploads or Git history.
Diagnostics directory/CA data is visible to all signed-in demo users when `DIAG_ADMIN_GRAPH=true`;
keep the app local and use synthetic content.

**Cleanup:** reset currently stops only ports 8000/16686. Stop additional instances by verified PID
and separately review/remove extra identities, DLP/CA policies, SITs, content-viewer membership
and billing resources. Generated configs/overlays can retain secrets and stale IDs.

## Rules you must not relearn

Each of these cost hours or days. The reasons are in the references.

- **Agent 365 export is separate from Jaeger.** Check Agent 365 delivery with `A365_VERBOSE=true` and `Test-Readiness.ps1`, never by looking at Jaeger.
- **A root `invoke_agent` span is mandatory.** `gen_ai.agent.id` must equal the agent identity app ID, which must also match the token `azp`.
- **Force `gen_ai.operation.name` to lowercase `chat`.** The SDK enum emits `"Chat"` and the exporter's allow-list silently drops it.
- **Turn off LangChain auto-instrumentation.** It floods views with duplicate `invoke_agent` spans. Use the manual scopes with an explicit `parent_context`, because `scope.start()` doesn't attach context.
- **OBO chain:** the user token Tc must have the blueprint audience (`api://<bp>/access_agent_as_user`). T1 is a raw POST with `fmi_path=<agentId>`, because MSAL Python lacks `fmi_path`. The OBO request uses `client_id=<agentId>` and `client_assertion=T1`.
- **Expired user tokens:** they cause AADSTS500133 and endless exporter 401s. Refresh silently through the per-session MSAL cache, and have the resolver return `None` when the token is expired.
- **The `a365 --authmode obo` grants are `Principal`-only** (just the admin who ran setup). Add tenant-wide consent or inheritable permissions so other users work.
- **Inheritable permissions need `a365 setup permissions custom`.** The `az` token lacks `AgentIdentityBlueprint.UpdateAuthProperties.All`. Read them back via `/beta/applications/{bp}/microsoft.graph.agentIdentityBlueprint/inheritablePermissions`.
- **The Entra Permissions blade doesn't show inherited permissions.** It lists only explicit grants. Run `Sync-AgentPermissions.ps1` so each agent identity holds the blueprint's AllPrincipals grants and app roles.
- **Every `a365` call hits api.nuget.org** for an update check, which Defender network protection blocks with a toast. Use Graph for read-only checks.
- **Graph `$filter` on `oauth2PermissionGrants`** supports only clientId, resourceId and principalId. Never put a non-GUID placeholder in an `appId eq` filter.
- **PowerShell variable names are case-insensitive.** `$g` shadowed `$G` (the Graph base URL) and killed the OtelWrite grant. Use distinctive names.
- **Never call Graph through `az rest` on Windows** when the URL contains `?` or `(`, because cmd mangles it. Get a token and use `Invoke-RestMethod`.
- **Global Admin can't see prompt or response text in Purview.** Grant the *Data Security AI Content Viewers* role group (`Grant-PurviewContentViewer.ps1`), then sign out and back in after 15 to 30 minutes. `execute_tool` rows never carry content, by design.
- **Restarting the app clears in-memory sessions.** Tell the presenter to sign in again.
- **`Settings` is a frozen dataclass.** In tests, patch with `dataclasses.replace(settings, …)` and `patch.object(<module>, "settings", …)` in every module that imported it.
- **Stop processes by exact PID** (`Get-NetTCPConnection -LocalPort 8000`), never by name.

## Reference files

| Read when | File |
|---|---|
| Explaining or changing the architecture or token flow | `references/architecture.md` |
| Phase 1: adapting the template to a new domain | `references/customize.md` |
| Phase 2: a365 CLI, Entra wiring, registry checks | `references/registration.md` |
| Phase 3: SDK details, verification queries, Purview roles | `references/observability.md` |
| Phase 4: Purview SDK calls, statuses, DLP policy | `references/purview-dlp.md` |
| Phase 5: CA design for users and agent identities | `references/conditional-access.md` |
| Phase 6: Diagnostics tab internals | `references/diagnostics.md` |
| Anything fails | `references/troubleshooting.md` |

## Maintaining the template

After improving a reference implementation, refresh the bundled template with:
`python <skill>\scripts\build_template.py <repo> --agent-name <its name> --provider "<its provider>" --domain <tenant domain> --admin <upn prefix>`.
It strips the `.env` file, generated configs and tenant GUIDs, and reports any leftovers.
