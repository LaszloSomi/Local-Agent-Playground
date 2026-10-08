# Observability (Agent 365 SDK via microsoft-opentelemetry)

Learn: https://learn.microsoft.com/microsoft-agent-365/developer/observability?tabs=python. The samples there use the
older `microsoft_agents_a365.observability` package. Its banner recommends the **Microsoft OpenTelemetry distro**
(`microsoft-opentelemetry[langchain]`, validated 1.3.9), which has the same scopes under `microsoft.opentelemetry.a365.core`.

## Wiring (agent/telemetry.py)

`use_microsoft_opentelemetry(enable_a365=True, a365_contextual_token_resolver=resolver, span_processors=[Jaeger BatchSpanProcessor], instrumentation_options={langchain/openai-agents disabled}, resource=…)`.

- **Exporter gating:** SDK 1.3.9 ORs the kwarg with the `ENABLE_A365_OBSERVABILITY_EXPORTER` env var, so passing `False` doesn't win
  if the env var is true. The template syncs the env var from `.env` before init.
- **Route (OBO):** `https://agent365.svc.cloud.microsoft/observability/tenants/{tid}/otlp/agents/{agentId}/traces?api-version=1`.
  The `/observabilityService/` path is for S2S only, not this flow.
- **Success logging:** the SDK logs successful exports only at DEBUG (`Exporting N spans to endpoint`, followed by the response with
  per-sink `sent` status). Set `A365_VERBOSE=true` to raise the `microsoft.opentelemetry` logger to DEBUG. Any 2xx counts as
  delivered, and the response body is truncated to 200 characters.

## Scopes used (manual instrumentation)

| Scope | Where | Content |
|---|---|---|
| `InvokeAgentScope` (root, **required**) | `/api/chat` | input and output messages, `CallerDetails`/`UserDetails(user_client_ip=valid IP)` |
| `InferenceScope` | `A365Callbacks.on_chat_model_start/on_llm_end` | messages, model, provider, token usage, `server.address/port` |
| `ExecuteToolScope` | `on_tool_start/on_tool_end` | tool name, arguments and result. **Never prompt text** (by design) |
| `OutputScope` | not used | only for async outputs that finish after the parent span |

Implementation rules:
- **Parent context:** use `scope.start()` plus `dispose()` with `SpanDetails(parent_context=…)`. `start()` doesn't attach context; only `__enter__` does.
- **Lowercase `chat`:** `InferenceOperationType.CHAT.value == "Chat"`, but the exporter allow-list wants `chat`. Call `scope.set_tag_maybe("gen_ai.operation.name","chat")`.
- **Session propagation:** child scopes copy the conversation but not the session ID. `BaggageBuilder` carries the tenant, agent, conversation and session.
- **Agent user attributes:** don't populate `microsoft.agent.user.*` with the human user. A third-party OBO agent has no agentic user.
- **Prompt suppression:** `A365_SUPPRESS_INVOKE_AGENT_INPUT` (default false) strips the prompt from `invoke_agent`. Check it first if prompts disappear after an upgrade.

## Silent-drop conditions (check in this order)

1. The exporter is off, or `.env` still has placeholders. `Test-Readiness.ps1` catches this. **Jaeger works regardless.**
2. No user in the tenant has an **assigned** E7 or Agent 365 license. The service returns 200 with `tenant_not_licensed`.
3. There's no root `invoke_agent` span, or `gen_ai.agent.id` ≠ the agent identity appId ≠ the token `azp`.
4. Inference spans carry `Chat` instead of `chat`.
5. The token resolver returned `None` ("No token resolved"), or the token expired (AADSTS500133, then 401 retries). Sign in again.
6. The `OtelWrite` grant or inheritance is missing for that user (the CLI grant is Principal-only).

## Verification surfaces

- **Jaeger:** http://localhost:16686, service = agent name. Use the v2/v3 API: `/api/v3/traces?query.service_name=…&query.start_time_min=<RFC3339>&query.start_time_max=<RFC3339>`.
- **Defender:** Advanced hunting:
  ```kql
  CloudAppEvents
  | where Timestamp > ago(1h)
  | where ActionType in ("InvokeAgent","InferenceCall","ExecuteToolBySDK")
  | extend d = parse_json(RawEventData)
  | project Timestamp, ActionType, AccountDisplayName, d.AgentId, d.ConversationId, d.ToolName
  | order by Timestamp desc
  ```
- **Purview:**
  - DSPM (for AI) → Activity explorer → **AI activities** tab. The classic DLP Activity explorer never shows agents.
  - Audit search is the authoritative source.
  - Each question produces several rows: `invoke_agent` and inference rows carry content; *Execute Tool by SDK* rows show
    "Related activity not found" for the interaction details. That's expected.
- **M365 admin center:** Agents → the agent → activity.

## Purview roles (presenter must know)

| Need | Role |
|---|---|
| See AI activity rows | Global Admin / Compliance Admin are enough |
| See prompt and response **text** | Purview role group **Data Security AI Content Viewers** (least privilege) or **Content Explorer Content Viewer**. GA is ✕ |
| "Your role can't view AI Visits or user risk levels" banner | Insider Risk Management Analyst or Investigator (a separate issue) |

These are **Purview role groups, not Entra roles**, so they don't appear in PIM. Avoid *Purview Workload Content
Administrator*, which is managed by the role migrator and gets overwritten. After a change, wait 15 to 30 minutes, then fully sign out and back in.
`scripts/Grant-PurviewContentViewer.ps1 [-CheckOnly]` handles this. It needs `Connect-IPPSSession` in a real console because of WAM.
