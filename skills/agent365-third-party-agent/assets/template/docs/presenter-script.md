# Presenter script: Agent 365 third‑party agent registry

> 📊 Diagrams for this walkthrough: [00-diagrams.html](00-diagrams.html) (open in a browser)

**Audience:** IT, security and compliance leads (optionally developers). **Length:** about 35 minutes plus Q&A.
**Story:** "Your developers already build agents outside Microsoft's stack. Agent 365 gives each one an
identity, puts it in the registry, makes it visible to Defender and Purview through the Observability SDK, and puts
its prompts and responses under Purview policy through the Purview SDK, without re‑platforming."

## Before the call (T‑60 min)

### Three-agent comparison (intended setup)

Use [diagram 11](00-diagrams.html#d11) to introduce the comparison. **DemoAgent1/2/3 are talk-track labels**, not tenant renames:
Agent1 maps to `{{AGENT_NAME}}`; Agent2 maps to the existing second instance; Agent3 is a proposed local-only instance.

| Capability / expected result | DemoAgent3: local baseline | DemoAgent2: observable | DemoAgent1: governed |
|---|---|---|---|
| Local UI | Proposed :8002 | :8001 | :8000 |
| Entra sign-in / Agent 365 registry | No integration | Yes, shared blueprint | Yes, shared blueprint |
| Observability | Local Jaeger only | Jaeger + Agent 365 | Jaeger + Agent 365 |
| Purview SDK | Disabled | Enabled | Enabled |
| Dallas-block DLP policy | No Purview enforcement | Not targeted | Applied |
| Dallas prompt | Answer returned* | Answer returned* | Blocked before LLM/tools |
| San Francisco prompt | Answer returned* | Answer returned* | Answer returned* |

*Expected with healthy model/tools and no other blocking policy.

**This is the intended demonstration, not the current configuration.** Agent3 has not been created, and
`New-PurviewDlpDemo.ps1` currently targets both registered instances. Before presenting, create a separate
local-only overlay with `AUTH_ENABLED=false`, `ENABLE_A365_OBSERVABILITY_EXPORTER=false` and `PURVIEW_ENABLED=false`,
and scope the Dallas-block policy to Agent1 only. Do not use the current all-instance DLP script unchanged
for this comparison. Keep the Purview SDK enabled for Agent2: the contrast must be policy targeting, not a disabled SDK.
Optionally target Agent2 with a collection policy to show prompt/response content without the Dallas-block rule.

- [ ] All three UIs ready; verify the same Dallas and San Francisco prompts against each
- [ ] Verify Agent1 is targeted by the Dallas-block policy and Agent2 is not; check for other blocking policies
- [ ] Diagnostics: Agent3 has no Entra tokens; Agent1/2 have their own agent IDs and SDK tokens
- [ ] Pre-stage Agent1/2 telemetry; show distinct identities in the registry and Defender

> "We will compare the same weather agent at three levels: local, observable, and governed.
> First Agent3 works without Agent 365 integration. Then Agent2 has an identity and visibility.
> Finally Agent1 adds the targeted Dallas-block policy. The blueprint shares permissions;
> it does not require every agent to have the same DLP policy scope."

### Existing walkthrough readiness

- [ ] `.\scripts\run.ps1` running; <http://localhost:8000> and <http://localhost:16686> open
- [ ] Signed in as **Alice**; ran 3–4 prompts (SF weather, Redmond address geocode, Paris) → **pre‑stages Defender/Purview data**
- [ ] Browser tabs (signed in as GA, in a separate profile): Entra admin center (Agent ID), M365 admin center (Agents),
      Defender advanced hunting (query from 03 pasted in), Purview Audit
- [ ] `.env` `AGENT365_AGENT_ID` copied into the KQL
- [ ] Fallback: if the tenant is slow, screenshots of Defender/Purview from the pre‑stage run

> If you're showing registration **live** (Part 2), reset first: `.\scripts\reset.ps1 -Tenant`, then pre‑stage
> telemetry with a *second* agent name, or rely on screenshots for Part 4.

---

## Part 1: The agent (5 min)

**Show:** DemoAgent3, the local-only baseline (after preparing it); code tree in VS Code.

> "This is a deliberately simple third‑party agent: Python, LangGraph, and a local open‑source model
> running in Ollama. No Azure, no Copilot Studio, no Foundry. It has two tools, current weather and
> geocoding, and calls public APIs."

- Ask: *"What's the weather in San Francisco?"* → point at **tools: get_weather**
- Ask: *"What's the weather in Dallas?"* → answer returned; no Purview enforcement on this instance
- Ask: *"Geocode 1 Microsoft Way, Redmond, WA"*
- Switch to Jaeger → open the trace → root `invoke_agent`, children `Chat` and `execute_tool`

> "It's already emitting standard OpenTelemetry with the GenAI conventions. Right now only my laptop can see
> it. The question every CISO asks is: *what agents are running, who's using them, and what are they doing
> with our data?*"

## Part 2: Give it an identity and register it (10 min)

**Show:** terminal + Entra admin center + M365 admin center. Follow [02-register.md](02-register.md).

1. `a365 setup requirements`
2. `a365 setup all -n {{AGENT_NAME}} --authmode obo --dry-run` → read the plan aloud
3. The real run (≈2–3 min). While it runs:
   > "Entra Agent ID introduces a **blueprint**, which is the template and holds credentials. Agent **identities**
   > are created from it and are what the agent runs as. They're first‑class identities: sign‑in logs,
   > Conditional Access, lifecycle, sponsors, all of it."
4. **Checkpoint A:** Entra → Agent ID → the blueprint and the identity. Copy the identity's App ID.
5. **Checkpoint B:** MAC → Agents → All agents → the agent is in the **registry**.
6. `.\scripts\Configure-Entra.ps1` → explain two lines: "the web app signs the user in; the agent then acts
   **on behalf of** that user", and "the blueprint now has **inheritable permissions** for the Observability SDK
   and the **Purview SDK**, so every agent identity built from it can send telemetry and ask Purview for a policy decision."

**Key message:** registration takes minutes, runs from a CLI, and doesn't change where or how the agent runs.

## Part 3: Users, OpenTelemetry and the Purview SDK (5 min)

**Show DemoAgent2 first**, signed in as Alice. Ask the same Dallas question: an answer is expected because
this instance is outside the Dallas-block policy, **not** because the Purview SDK is disabled.
Show its registry entry, Diagnostics tokens and agent ID, and pre-staged Agent 365 telemetry.
Do not promise prompt/response visibility without a covering collection policy.

1. On DemoAgent2 (`run.ps1 -EnvFile .env.agent2`), **sign in** as Alice → *"weather in San Francisco"* → *telemetry: token-ok* and a `purview:` status
2. Jaeger: the same trace now carries Alice's `user.id` / `user.email` and the agent identity in `gen_ai.agent.id`
3. Entra → Sign‑in logs → Alice → WebClient; Agent sign‑ins → the agent identity acting for Alice

> "The agent uses two Microsoft SDKs, on two separate channels. The **Observability SDK** sends the same OpenTelemetry
> spans to Agent 365, authenticated as the agent on behalf of Alice. That's activity telemetry. The **Purview SDK**
> sends Alice's prompt, and the agent's answer, to Purview *before* the model runs and *before* the answer is shown,
> and Purview says allow or block. OpenTelemetry doesn't enforce anything; the Purview SDK does."

## Part 4: Security and compliance see it (10 min)

Follow [03-observability.md](03-observability.md) §2–§4. Use the pre‑staged data.

1. **Defender** advanced hunting → query 1 → expand an `ExecuteToolBySDK` row → the tool and arguments
   > "Your SOC can hunt across agent actions in the same place as email and endpoint telemetry."
2. **Defender** AI agents view → the agent's activity
3. **Purview** Activity explorer → Alice's interaction → the prompt and the response
   > "The prompt and answer text got here through the Purview SDK (`processContent`), and DSPM for AI classifies it."
4. **MAC** → agent → Activity and **Security** tabs → *(optional)* **Block** the agent → retry in the UI → it fails → unblock

## Part 5: Governed agent comparison and what's next (3 min)

Switch to **DemoAgent1**, with the same user and the same Dallas prompt. Show the Purview block before the
LLM/tools run, then ask for San Francisco and show the successful answer. Open Diagnostics and the
Purview policy evidence. Revisit diagram 11: Agent3 is local; Agent2 is registered and observable;
Agent1 is registered, observable and governed by this rule.

> "The code and model are the same. Agent1 and Agent2 share a blueprint and the SDK permissions,
> but the Dallas-block policy targets only Agent1. The difference is the policy scope, not a hard-coded city check."

> "Because the agent has an identity and runs on behalf of a user, the policies you already have apply to it:"

- **DLP** (built in via the Purview SDK): San Francisco is allowed and Dallas is blocked on Agent1 by the targeted Purview policy, not code. For this comparison, do not run the current all-instance `scripts\New-PurviewDlpDemo.ps1` unchanged.
- **Conditional Access** (Phase 2): Alice on a compliant device or the corporate network can use the agent; Bob on an
  unmanaged device or network is blocked at sign‑in.

## Likely questions

| Q | A |
|---|---|
| Does the agent have to run in Azure? | No. This one runs on a laptop; production can be any cloud or on‑prem. Only the identity and telemetry go to Microsoft. |
| What does the agent need to change? | Add the Agent 365 Observability SDK exporter (≈50 lines here), the Purview SDK calls before and after the LLM (`agent/purview.py`, `agent/guard.py`), and acquire tokens as the agent identity. The business logic doesn't change. |
| Is the prompt content sent to Microsoft? | Yes, on two channels. The **Purview SDK** (`processContent`) sends prompt and response text to Purview, which applies DLP and DSPM for AI. OpenTelemetry spans carry activity telemetry for Defender, Purview Audit and the admin center, but OTel doesn't evaluate or enforce prompt/response policies. |
| Which license? | Identity needs only Agent 365 enabled. Observability needs at least one E7/Agent 365 license assigned in the tenant. DLP for AI needs Purview PAYG. |
| OBO vs S2S? | OBO for user‑driven agents (per‑user attribution and CA). S2S for autonomous or background agents. |
| What about secrets? | The demo uses 30‑day client secrets. Production uses certificates or managed identity (federated credentials). |
