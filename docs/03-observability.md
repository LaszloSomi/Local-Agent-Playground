# 03 – OpenTelemetry → Defender, Purview and the M365 admin center

> 📊 Diagrams for this walkthrough: [00-diagrams.html](00-diagrams.html) (open in a browser)

Goal: show that **one OpenTelemetry trace** from a third‑party agent running on a laptop becomes
security telemetry (Defender), compliance and audit data (Purview) and inventory/activity data (M365 admin
center), with each run attributed to the **agent identity** and the **signed‑in user**.

> 🛡 **OpenTelemetry is activity telemetry only.** It doesn't evaluate or enforce prompt/response policies.
> Prompts and responses go from the LangGraph agent to Purview through the **Purview SDK** (`processContent`),
> which returns allow/block (DLP) and puts the prompt and response text into Activity explorer. See
> [05-phase2-dlp-ca.md](05-phase2-dlp-ca.md) section A. Both SDKs are enabled during registration
> ([02-register.md](02-register.md) step 4).

> ⏱ **Latency:** Jaeger shows traces instantly. Defender usually needs 5–30 minutes and Purview audit
> can take up to about an hour. **Run a few prompts as Alice at least an hour before the customer call**,
> then run a fresh one live and switch to the pre‑staged data in the portals.

## 0. Pre‑flight

**Run this first.** It checks the local configuration prerequisites, not live delivery:

```powershell
.\scripts\Test-Readiness.ps1
```

A passing result does not verify credentials, consent, export responses, or Purview content access.
An incomplete result lists missing settings; it does not inspect the running process's environment.
Use the exporter response and matching audit records to verify actual ingestion.

| Check | Why |
|---|---|
| At least one user has an **E7 or Agent 365 license assigned** | Without it the exporter gets HTTP 200, but the data is dropped (`tenant_not_licensed`) |
| **Purview auditing is on** (Purview → Audit → "Start recording…" is not shown) | Purview surfaces depend on the unified audit log |
| Defender **advanced hunting** access (Security Reader or higher) | For the `CloudAppEvents` queries |
| Your demo account is in **Data Security AI Content Viewers** (and you've signed out/in since) | Required to read **prompt and response text** in Activity explorer. **Global Administrator is NOT enough**, and it is not obtainable via PIM — it's a Purview role group. Check with `.\scripts\Grant-PurviewContentViewer.ps1 -CheckOnly`. See [§3a](#3a-i-can-see-the-activity-but-not-the-prompt-or-the-answer) |
| `.env` has `AUTH_ENABLED=true`, `ENABLE_A365_OBSERVABILITY_EXPORTER=true` | Set by `Configure-Entra.ps1`. **Until this runs, nothing reaches Microsoft 365** — the app logs `Agent 365 exporter enabled: False` at startup and only Jaeger receives spans |
| `.\.venv\Scripts\python -m agent.verify_spans` → `4/4 exportable`, root `invoke_agent`, `All required attributes present: True` | A missing root span means the data never reaches the MAC, Purview or Defender agent views. The audit also proves prompts/answers are being recorded |
| `.env` has `A365_VERBOSE=true` | Shows export attempts and HTTP responses in the `run.ps1` window. An attempt alone does not prove delivery — see [Proving the export actually happened](#proving-the-export-actually-happened) |

## 1. What is emitted (show this in Jaeger first)

```
invoke_agent Laszlo-AgentRegistryDemo1        ← InvokeAgentScope (root, one per user turn)
 ├─ Chat qwen2.5:7b                            ← InferenceScope   (LLM decides to call a tool)
 ├─ execute_tool get_weather                   ← ExecuteToolScope (Open-Meteo call, arguments + result)
 └─ Chat qwen2.5:7b                            ← InferenceScope   (LLM writes the answer)
```

Every span carries `gen_ai.agent.id` (the agent identity app ID), `gen_ai.conversation.id`,
`microsoft.tenant.id`, `microsoft.a365.agent.blueprint.id`, `user.id` / `user.email` / `user.name`
(from the Entra token) and `client.address`.

### Are we using the Agent 365 Observability SDK? Yes — manual instrumentation

This is the question customers ask, so be ready for it. The agent does **not** rely on
auto-instrumentation. It uses the **manual instrumentation** scopes from the
[Observability SDK](https://learn.microsoft.com/microsoft-agent-365/developer/observability?tabs=python),
via the **Microsoft OpenTelemetry Distro** (`microsoft-opentelemetry`) that supersedes the older
`microsoft_agents_a365.observability` package. Mapping:

| Learn scope | Where we call it | Carries prompt / answer |
|---|---|---|
| `InvokeAgentScope` | `agent\app.py` → `/api/chat` (root span, one per user turn) | ✅ `record_input_messages` + `record_output_messages` |
| `InferenceScope` | `agent\a365_callbacks.py` (per LLM call) | ✅ messages in and out |
| `ExecuteToolScope` | `agent\a365_callbacks.py` (per tool call) | ❌ by design — carries `gen_ai.tool.call.arguments` / `.result` instead |
| `OutputScope` | **not used, and not needed** | — |

`OutputScope` exists only for asynchronous agents whose answer isn't available while the parent scope
is still open. Ours is synchronous, so `InvokeAgentScope` records the output itself. Adding
`OutputScope` would duplicate the answer, not reveal it.

So **prompt capture is implemented**, but local capture alone does not prove that the cloud content
pipeline stored or linked the messages. Inspect export routing results and server-side records before
attributing missing details to viewer permissions — see [§3a](#3a-i-can-see-the-activity-but-not-the-prompt-or-the-answer).

Prove it in one command, without a tenant or a portal:

```powershell
.\.venv\Scripts\python -m agent.verify_spans
```

It runs one real turn against an in-memory exporter, applies the SDK's own export filter, and audits
every span against the [canonical attribute reference](https://learn.microsoft.com/microsoft-agent-365/developer/observability-attribute-reference).
It disables cloud export, uses a loopback test-client IP, and exits nonzero on HTTP errors, empty spans,
missing/empty required fields, invalid message arrays, or inconsistent run correlation:

```
Total spans: 4   exportable to Agent 365: 4
  SEND  chat           io Chat qwen2.5:7b
  SEND  execute_tool   -  execute_tool get_weather
  SEND  chat           io Chat qwen2.5:7b
  SEND  invoke_agent   io invoke_agent Laszlo-AgentRegistryDemo1

Root invoke_agent present: True
All required attributes present: True
Consistent run correlation: True
PASS: local span validation only; no cloud delivery was tested.
```

The `io` column is the point: `invoke_agent` and `chat` carry input **and** output messages;
`execute_tool` carries neither. `microsoft.agent.user.id` / `.email` are omitted because this agent
has no agentic user; OBO by itself does not determine whether an agent has one. The canonical spec
requires those fields only for embodied agents. Do not substitute the human caller into them.

### SDK audit corrections (September 30, 2026)

The live Entra reads confirmed an enabled agent identity linked to the configured blueprint,
the enabled `access_agent_as_user` scope, the localhost callback, and tenant-wide delegated
`Agent365.Observability.OtelWrite` consent. No tenant permissions were changed.
The CLI file still has `completed=false`, but a follow-up check confirmed setup itself: the CLI log
shows all eight `a365 setup all` phases finished (“Setup completed successfully”), `a365 setup all
--dry-run` would reuse the blueprint, identity and registration, and the registry entry
(`GET /beta/copilot/agentRegistrations/{id}`) links to this agent identity and blueprint. See
[02-register.md](02-register.md#verify-cli-setup-and-registry-read-only).

Corrections made before another cloud run:

- Propagate session, conversation, and channel via `BaggageBuilder`. SDK 1.3.9 child scopes do not
  copy `Request.session_id` themselves; previous audit exports showed different root/child sessions.
- Supply model endpoint fields on inference spans and the local function endpoint on tool spans.
  The canonical contract requires `server.address` and `server.port` on these operations too.
- Keep the process-level exporter environment variable consistent with the authentication gate.
  SDK 1.3.9 ORs that variable with the kwarg, so passing `False` alone does not disable cloud export.
- Strengthen the local verifier and remove readiness messages that incorrectly claimed delivery.

These changes remove local gaps; none is a confirmed fix for Purview's `Related activity not found`.
Restart and sign in for a new trace before comparing cloud records. Old records will not be repaired.
The token cache remains a single-presenter demo design (latest token per agent/tenant), not a
production multi-user or unattended-replay authentication design.

Run deterministic, local-only regression tests (including the actual SDK serialization with HTTP mocked):

```powershell
.\.venv\Scripts\python -m unittest discover -s tests -v
```

### Finding those attributes in the Jaeger UI

They are **span attributes**, not part of the span name, so they aren't visible on the timeline.
Click the `invoke_agent …` row to expand it, then open **Tags**. `gen_ai.agent.id` and
`microsoft.tenant.id` are listed there alphabetically.

Before `Configure-Entra.ps1` runs you'll see placeholders, because `.env` has no real IDs yet:

```
gen_ai.agent.id    = local-dev-agent
microsoft.tenant.id = local-dev-tenant
user.id            = local-user
```

After it runs (or after `.\scripts\Configure-Entra.ps1 -IdsOnly`, which needs no admin sign-in) and a restart:

```
gen_ai.agent.id     = <agent-identity-app-id>   ← matches "All agent identities" in the Entra admin center
microsoft.tenant.id = <tenant-id>
```

`user.*` stays `local-user` until `AUTH_ENABLED=true`, because there is no signed-in user to attribute to.

To read them from the command line instead:

```powershell
$now = [DateTimeOffset]::UtcNow
$min = $now.AddMinutes(-30).UtcDateTime.ToString("yyyy-MM-ddTHH:mm:ssZ")
$max = $now.UtcDateTime.ToString("yyyy-MM-ddTHH:mm:ssZ")
$r = Invoke-RestMethod "http://localhost:16686/api/v3/traces?query.service_name=Laszlo-AgentRegistryDemo1&query.start_time_min=$min&query.start_time_max=$max"
($r.result.resourceSpans.scopeSpans.spans | Where-Object name -like 'invoke_agent*' | Select-Object -Last 1).attributes |
  ForEach-Object { "{0} = {1}" -f $_.key, $_.value.stringValue }
```

> Jaeger v2 requires RFC 3339 timestamps on `/api/v3/traces`; Unix seconds return HTTP 400.

*Talk track:* "This is plain OpenTelemetry with the GenAI semantic conventions. Any agent in any
framework can emit it; we just add the Agent 365 exporter. Here it's going to Jaeger on this laptop
**and** to Microsoft 365 at the same time."

### Where the export goes

```
POST https://agent365.svc.cloud.microsoft/observability/tenants/{tenantId}/otlp/agents/{agentId}/traces?api-version=1
Authorization: Bearer <OBO token: azp = agent identity, user = signed-in user, scope Agent365.Observability.OtelWrite>
```

#### Proving the export actually happened

The SDK logs export attempts and successful HTTP responses at **DEBUG**. Silence at the normal
log level does not prove either success or failure.

Set `A365_VERBOSE=true` in `.env` and restart the app to see export diagnostics such as:

```
DEBUG microsoft.opentelemetry.exporter.agent365_exporter: Exporting 4 spans to endpoint: https://agent365.svc.cloud.microsoft/observability/...
```

That line is emitted **before token resolution and the HTTP request**. It proves an export attempt,
not delivery. In SDK 1.3.9, look for the subsequent `HTTP 200 success. Correlation ID: ... Response: ...`
line (or another 2xx status), inspect the response body, and retain the correlation ID for diagnosis.
HTTP success still does not prove downstream Purview content ingestion, correlation, or viewer access.
Confirm those separately by opening the matching interaction in Purview. A local Jaeger trace with
input/output messages proves content capture, not that Purview stored or can display that content.

Set `A365_VERBOSE=false` for a quieter console during the live demo — the distro emits a fair amount
of unrelated DEBUG noise at startup (`Instrumentation skipped for library …`, which is expected and
harmless: we instrument manually rather than relying on auto-instrumentation).

## 2. Microsoft Defender

### 2a. Advanced hunting (fastest; raw events)

<https://security.microsoft.com> → **Hunting → Advanced hunting**:

```kql
// Every Agent 365 event from this demo agent, newest first
let agentId = "<AGENT365_AGENT_ID from .env>";
CloudAppEvents
| where Timestamp > ago(24h)
| where ActionType in ("InvokeAgent", "InferenceCall", "ExecuteToolBySDK")
| where RawEventData has agentId
| project Timestamp, ActionType, AccountDisplayName, AccountObjectId, IPAddress, RawEventData
| order by Timestamp desc
```

```kql
// One row per conversation turn: who asked, which tools ran
let agentId = "<AGENT365_AGENT_ID>";
CloudAppEvents
| where Timestamp > ago(24h) and RawEventData has agentId
| extend Raw = todynamic(RawEventData)
| extend Conversation = tostring(Raw.ConversationId), Tool = tostring(Raw.ToolName)
| summarize Turns = countif(ActionType == "InvokeAgent"),
            LlmCalls = countif(ActionType == "InferenceCall"),
            Tools = make_set_if(Tool, ActionType == "ExecuteToolBySDK" and isnotempty(Tool)),
            Users = make_set(AccountDisplayName)
            by Conversation
```

```kql
// Agent inventory as Defender sees it
AgentsInfo
| where AgentId == "<AGENT365_AGENT_ID>" or AgentName has "Laszlo-AgentRegistryDemo1"
```

Expand `RawEventData` on an `ExecuteToolBySDK` row to show the tool name, the arguments
(`{"place": "San Francisco"}`) and the result. That's the same content you saw in Jaeger.
Field names map directly from span attributes: `AgentId` ← `gen_ai.agent.id`, `ConversationId` ← `gen_ai.conversation.id`.

> If the RawEventData field names differ in your tenant, run the first query, expand one row, and adjust
> the `Raw.<Field>` names in the second.

### 2b. Agent views

Defender portal → **Assets → AI agents** (or *Cloud apps → AI agents*, depending on your preview ring) →
`Laszlo-AgentRegistryDemo1`: activity timeline, tools used, users. These views require the root
`invoke_agent` span.

*Talk track:* "The SOC sees this third‑party agent the same way it sees Copilot agents. It's in the
same tables, so detections, custom rules and incident correlation all work."

## 3. Microsoft Purview

<https://purview.microsoft.com>

1. **Audit** → *New search* → Date range: today; *Users*: Alice; Workloads/Record types: filter to
   Agent 365 / AI app interactions (or search all and filter by the agent name). Open a record to show
   the prompt, the agent identity and the timestamp.
2. **DSPM → Discover → Activity explorer → *AI activities* tab**: filter *AI app* / *Agent* =
   `Laszlo-AgentRegistryDemo1` to see the user→agent interactions, and any sensitive information types
   detected in prompts or responses.

   > ⚠️ This is **not** the classic Purview *Activity explorer* under Information Protection / DLP —
   > that one shows labeling and DLP matches only, and your agent will never appear there.
   > The AI surface lives under **Data Security Posture Management**, and in some tenants is still
   > labelled *DSPM for AI* / *AI observability*. Audit (step 1) is the authoritative surface;
   > check it first if Activity explorer looks empty.

### 3a. "I can see the activity but not the prompt or the answer"

Start with these two checks, but neither exhausts the possible causes.

**If the root Invoke Agent row says `Related activity not found`:** do not assume permissions or
a wrong-row selection. Compare the raw AuditData `TraceId`, `OpId`, `ParentId`, `ConversationId`,
`SessionIdentity`, `RequestId`, and `ResponseId` with Jaeger. Matching activity metadata does not prove
content storage or retrieval. If a new, validated run still fails, retain the root record and
request/response IDs for Agent 365/Purview support.

**Cause 1 — you opened a tool row.** Not every *AI Interaction* row carries prompt text:

| Activity (Purview) | Span | Carries prompt / answer? |
|---|---|---|
| *Execute Tool by SDK* | `execute_tool` | ❌ No — by design. It has tool name, tool type, tool ID and description only. |
| the invoke-agent row | `invoke_agent` | ✅ **Yes** — `gen_ai.input.messages` + `gen_ai.output.messages` |
| the inference row | `Chat` | ✅ Yes — the per-LLM-call messages |

One question produces several rows at the same timestamp. If the details pane shows **Tools accessed**
with a *Tool name*, you're on the `execute_tool` row — close it and open a sibling row from the same
second. You can confirm what the agent actually emitted at any time in Jaeger:

```powershell
$end   = (Get-Date).ToUniversalTime().ToString("yyyy-MM-ddTHH:mm:ssZ")
$start = (Get-Date).ToUniversalTime().AddHours(-1).ToString("yyyy-MM-ddTHH:mm:ssZ")
$r = Invoke-RestMethod "http://localhost:16686/api/v3/traces?query.service_name=Laszlo-AgentRegistryDemo1&query.start_time_min=$start&query.start_time_max=$end"
foreach ($s in $r.result.resourceSpans.scopeSpans.spans) {
  $op    = ($s.attributes | Where-Object key -eq 'gen_ai.operation.name').value.stringValue
  $hasIn = ($s.attributes | Where-Object key -eq 'gen_ai.input.messages') -ne $null
  "{0,-42} op={1,-14} input={2}" -f $s.name, $op, $hasIn
}
```

Expected: `invoke_agent` and `Chat` show `input=True`; `execute_tool` shows `input=False`.

**Cause 2 — your role can't see content.** Purview deliberately separates *"an AI interaction
happened"* from *"here is what was said."* **Global Administrator does not grant the second one.**

This surprises people, so it's worth being precise. In Microsoft's permission table, GA is ✓ for
*"View all events in activity explorer, AI activities tab"* — which is why you **do** see the agent,
the tool calls and the timestamps. But for the content row, **every column is ✕**:

> *View the prompts and responses within **AI Interaction** events from activity explorer*
> — ✕ Entra Compliance Administrator · **✕ Entra Global Administrator** · ✕ Purview Compliance
> Administrator role group · ✕ view-only roles · ✕ view-only-for-AI roles
> → *additional role group required:* **Content Explorer Content Viewer** or
> **Microsoft Purview Data Security AI Content Viewer**

([permissions reference](https://learn.microsoft.com/purview/data-security-posture-management-permissions))

No built-in role sees prompt content by default — it is always an **additive** grant, which is the
whole point of the control. The banner *"Additional permissions required. Your role can't view AI
Visits or user risk levels"* is a related but separate signal (that one is about *AI Visits* and user
risk, which need Insider Risk Management roles).

> ⚠️ **You cannot get this by elevating in PIM.** These are **Microsoft Purview role groups**,
> assigned in the Purview portal — not Entra roles, so they don't appear in *PIM → My roles*.
> In particular, the Entra role **Purview Workload Content Administrator** is *not* the answer: it is
> managed by the [Purview Role Assignment Migrator](https://learn.microsoft.com/purview/purview-role-assignment-migrator),
> syncs **out of** Purview rather than into it, covers SharePoint/Teams/OneDrive/Exchange content
> operations (search, purge, hold), and Microsoft explicitly says **not** to assign it directly in
> Entra — manual assignments get overwritten at the next sync.

Which of the two to use:

| Role group | Grants | Use when |
|---|---|---|
| **Data Security AI Content Viewers** (role: *Data Security AI Content Viewer*) | Extended prompt and response details in AI app interactions, in DSPM for AI | **Preferred** — least privilege, exactly what this demo needs |
| **Content Explorer Content Viewer** | Read actual content across Content Explorer (files, assets, AI interactions) | Broader; also needed for *file details in data risk assessments* and *file metadata in asset explorer* |

**Already granted it? Check without changing anything:**

```powershell
.\scripts\Grant-PurviewContentViewer.ps1 -CheckOnly
```

It reports membership of **both** qualifying roles and tells you whether prompt text should be
visible. Re-running the script normally is safe either way — it detects existing membership and
won't duplicate it.

Grant it either way.

**Portal (most reliable, and the better thing to show a customer):**
**purview.microsoft.com → Settings → Roles and scopes → Role groups →
*Data Security AI Content Viewers* → Edit → add your account**.
(Use *Content Explorer Content Viewer* instead if you also want asset/file content.)

**PowerShell:**

```powershell
.\scripts\Grant-PurviewContentViewer.ps1 -CheckOnly   # am I already a member?
.\scripts\Grant-PurviewContentViewer.ps1 -WhatIf      # preview
.\scripts\Grant-PurviewContentViewer.ps1              # grant
```

> Run it from a **normal interactive terminal window**. Security & Compliance PowerShell signs in
> through WAM, which needs a real console window handle; inside an embedded/background terminal it
> fails with *"A window handle must be configured"*. The script automatically retries with
> `-DisableWAM`, but a headless host has no interactive sign-in at all — use the portal there.

> Allow 15–30 minutes, then **sign out of the Purview portal completely and sign back in** — the role
> is baked into your session token, so refreshing the page is not enough.

> 💡 *This is a good demo beat, not a defect.* It shows Purview enforcing least privilege over AI
> content: an admin can prove an agent ran and which tools it touched without being able to read the
> user's prompt. Say that out loud instead of apologising for it.

3. Point out that the agent is **automatically** in scope for audit, data classification and Compliance
   Manager *Assessments for AI regulations*. For DLP, Insider Risk and Communication Compliance, you
   include the agent in the policy like a user (the San Francisco vs Dallas DLP demo in [05-phase2-dlp-ca.md](05-phase2-dlp-ca.md); the agent calls Purview `processContent`, which is also what puts the prompt/response text into Activity explorer).

*Talk track:* "Compliance didn't have to integrate anything. The agent's interactions landed in the
same audit log as Exchange and Teams, so retention, eDiscovery and DLP apply."

## 4. Microsoft 365 admin center

<https://admin.cloud.microsoft> → **Agents → All agents** → `Laszlo-AgentRegistryDemo1`

- **Overview**: publisher, blueprint and identity, owner or sponsor.
- **Activity**: runs and active users (from `invoke_agent` spans).
- **Security** tab (needs an E7/Agent 365 license): links to Purview *Activity explorer* and
  *AI observability* for this agent, and to compliance gaps.
- **Block / unblock**: show that an admin can stop this agent here. Then its sign‑in / OBO fails and no
  new traces arrive. It's a strong closing moment; unblock it before you leave.

## 5. Live demo sequence (condensed)

1. Split screen: agent UI (left) + Jaeger (right). Alice asks *"weather in San Francisco"* → the trace appears instantly.
2. Defender advanced hunting → run query 1 → show the pre‑staged rows (and the new row if it has arrived).
3. Purview Audit → an Alice record → the prompt text.
4. **DSPM → Activity explorer → AI activities** → open the **invoke-agent** row (not *Execute Tool by SDK*)
   for the prompt and answer, then open the tool row to show the `geocode` call. See [§3a](#3a-i-can-see-the-activity-but-not-the-prompt-or-the-answer).
5. MAC → Agents → the agent's Activity and Security tabs.

> **Pre‑flight the day before:** confirm your demo account is in **Data Security AI Content Viewers**
> (`.\scripts\Grant-PurviewContentViewer.ps1 -CheckOnly`) and that you've signed out/in since.
> Being Global Admin is **not** sufficient, and PIM elevation won't help. Without it step 4 shows
> metadata but no prompt text, and there is no way to fix it live — propagation takes 15–30 minutes.

## Troubleshooting

| Symptom | Likely cause | Fix |
|---|---|---|
| Jaeger shows `gen_ai.agent.id = local-dev-agent` and `microsoft.tenant.id = local-dev-tenant` | `.env` still has empty `AGENT365_AGENT_ID` / `AGENT365_TENANT_ID`, so `app.py` falls back to placeholders | Run `.\scripts\Configure-Entra.ps1` (or `-IdsOnly` for the IDs alone), then restart the app |
| Nothing in Purview / Defender / MAC at all, and startup logged `Agent 365 exporter enabled: False` | The exporter never ran — `ENABLE_A365_OBSERVABILITY_EXPORTER=false` and/or `AUTH_ENABLED=false` | Run the full `.\scripts\Configure-Entra.ps1`; local Jaeger works regardless, so this is easy to miss |
| Purview Activity explorer empty but Audit has records | Looking at the **classic** Activity explorer (Information Protection / DLP) instead of **DSPM → Discover → Activity explorer → AI activities**, or DSPM analytics not opted in | Use the DSPM path; opt in under **DSPM → Getting Started**. Audit is authoritative — check it first. See §3 |
| Activity explorer shows the interaction, tool name and agent ID, but **no prompt and no answer** | Either you opened the *Execute Tool by SDK* row (tool spans never carry prompt text), or your role lacks content access — **Global Administrator is not enough** | See [§3a](#3a-i-can-see-the-activity-but-not-the-prompt-or-the-answer). Open the invoke-agent row, and run `.\scripts\Grant-PurviewContentViewer.ps1` |
| Banner: *"Additional permissions required. Your role can't view AI Visits or user risk levels"* | Expected for GA/Compliance Admin. *AI Visits* and user risk need Insider Risk Management Analyst/Investigator; prompt content is a **separate** grant | Only grant what the demo needs — usually just the content viewer role. See §3a |
| *"I'm Global Admin, I should see everything"* | GA is ✓ for viewing AI activity rows but ✕ for prompt/response content — **every** role is ✕ there; it's always an additive grant | `.\scripts\Grant-PurviewContentViewer.ps1 -CheckOnly` to confirm, then run it without `-CheckOnly` |
| Elevated to a Purview-sounding role in **PIM** and prompts are still hidden | The required grants are **Purview role groups**, not Entra roles — they never appear in PIM. `Purview Workload Content Administrator` is sync-managed by the Role Assignment Migrator and is unrelated to AI prompt content | Assign **Data Security AI Content Viewers** in the Purview portal (Settings → Roles and scopes → Role groups) |
| Not sure whether the content viewer role was already granted | — | `.\scripts\Grant-PurviewContentViewer.ps1 -CheckOnly` — read-only, checks both qualifying roles. Re-running the grant is safe and idempotent |
| Added the content viewer role but prompts are still hidden | The role is in your session token | Sign **out** of purview.microsoft.com and back in; allow 15–30 min for propagation |
| *Interaction details: Related activity not found*, including on Invoke Agent | Content ingestion, correlation, or retrieval is not established by the activity row alone | Inspect the root, raw AuditData, and export routing results. Do not diagnose permissions solely from this message. See §3a |
| UI shows *telemetry: token-error: …* | OBO chain failed | See the `run.ps1` console for AADSTS; see [02 troubleshooting](02-register.md#troubleshooting) |
| Exporter `HTTP 401` | Token audience or scope wrong | The scope must be `9b975845-388f-4429-889e-eab1ef63949c/Agent365.Observability.OtelWrite` |
| `AADSTS500133: Assertion is not within its valid time range`, then repeated exporter `HTTP 401 retryable` | Before Sept 30 the app never refreshed the signed-in user's token, so after ~1 hour the OBO exchange failed and the exporter reused the last expired observability token. **That turn's telemetry never reached Agent 365** | Fixed: `/api/chat` refreshes the user token silently through MSAL; if that isn't possible it returns "Your sign-in expired" and does not run an untracked turn. The resolver never returns an expired token, so there are no more 401 retry loops |
| Exporter `HTTP 403` | `gen_ai.agent.id` ≠ token `azp` ≠ URL `agentId` | `AGENT365_AGENT_ID` must be the **agent identity** app ID, not the blueprint's |
| `HTTP 200` but nothing anywhere | No E7/Agent 365 license **assigned** | Assign a license to any user, wait, and retry |
| Exporter `HTTP 403` and `Configure-Entra.ps1` printed a red **FAILED to grant Agent365.Observability.OtelWrite** box | The tenant-wide consent for the agent identity wasn't created | Entra admin center → Enterprise applications → `<agent> Identity` → Permissions → Grant admin consent, then re-run `Test-Readiness.ps1` |
| Advanced hunting has rows, but no MAC, Purview or agent views | No root `invoke_agent` span | Run `agent.verify_spans`; the root must be `invoke_agent` |
| `agent.verify_spans` prints `HTTP 401 None` and `Total spans: 0` | Expected once `AUTH_ENABLED=true` — the test client has no session cookie, so `/api/chat` returns 401 before any span exists | Fixed: the module now forces `AUTH_ENABLED=false` for the offline check. Update the file if you see this again |
| An older `verify_spans` audit requires `microsoft.agent.user.id / .email` | This demo has no agentic user; these fields are conditional, not required for every OBO agent | Use the current verifier and canonical attribute reference. Do not fill them with the human caller |
| Customer asks whether the **Observability SDK** is really integrated | Manual instrumentation is less obvious than auto-instrumentation | Show the scope mapping table in §1 and run `verify_spans` — it validates against the Learn required-attribute tables |
| No errors in the console, but you can't tell whether anything was actually exported | Export attempts and HTTP success responses are logged at **DEBUG** | Set `A365_VERBOSE=true` in `.env`; inspect the HTTP response and body, not just `Exporting N spans`. See [Proving the export actually happened](#proving-the-export-actually-happened) |
| Console is full of `DEBUG … Instrumentation skipped for library <x>` at startup | Expected with `A365_VERBOSE=true` — we instrument manually, so the distro's auto-instrumentation has nothing to hook | Harmless. Set `A365_VERBOSE=false` for a quieter demo console |
| `/api/chat` returns **401** right after restarting the agent | Sessions are in-memory, so restarting invalidates the browser cookie | Reload <http://localhost:8000> and sign in again before chatting |
| `run.ps1` aborts with *Port 8000 is already in use by python (PID …)* | An earlier agent process is still listening | `Stop-Process -Id <PID>` using the PID in the message, then re-run |
| LLM spans missing in Defender, tool spans present | `gen_ai.operation.name` = `Chat` (capitalized) is dropped by the exporter allow‑list | Already handled in `a365_callbacks.py` (forced to lowercase `chat`); keep it if you upgrade the SDK |
| Everything attributed to one user | The token resolver caches the latest user's token per agent | Expected for a single‑presenter demo; for concurrent users, resolve per request |

## Implementation notes

- SDK: [`microsoft-opentelemetry`](https://pypi.org/project/microsoft-opentelemetry/) (the Microsoft
  OpenTelemetry distro with the A365 exporter). The older `microsoft-agents-a365-observability-*`
  packages are deprecated.
- `a365_use_s2s_endpoint=False`, so the OBO (user‑delegated) route is used.
- The exporter token comes from `agent/auth.py`:
  user token (aud = blueprint) → blueprint FMI token (`fmi_path` = agent identity) → OBO as agent identity.
  MSAL Python doesn't support `fmi_path`, so that hop is a raw token POST.
- References: [Observability concepts](https://learn.microsoft.com/microsoft-agent-365/developer/observability-concepts),
  [Observability SDK](https://learn.microsoft.com/microsoft-agent-365/developer/observability),
  [Defender: AI agent hunting](https://learn.microsoft.com/defender-xdr/security-for-ai/ai-agent-detection-protection),
  [Purview for Agent 365](https://learn.microsoft.com/purview/ai-agent-365),
  [MAC agent details](https://learn.microsoft.com/microsoft-365/admin/manage/agent-details).
