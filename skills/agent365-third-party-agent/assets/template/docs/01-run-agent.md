# 01 – Run the agent locally

Goal: the agent answers weather and geocode questions on your machine, and every turn produces an
OpenTelemetry trace you can see in Jaeger. No tenant is needed for this step.

## Prerequisites

- Windows 10/11 or Windows Server 2022+ (laptop or VM), 16 GB RAM recommended for the 7B model
- `winget` available (for the automatic installs)
- Internet access to `api.open-meteo.com`, `geocoding-api.open-meteo.com`, `nominatim.openstreetmap.org`
- For step 02: Azure CLI, .NET 8+ SDK, the `a365` CLI, and PowerShell 7 with `Microsoft.Graph.Authentication`

## 1. Setup (one time, idempotent)

```powershell
cd D:\Repos\Local-Agent-Playground
.\scripts\setup.ps1
```

| Step | What it does |
|---|---|
| Python 3.12 | `winget install Python.Python.3.12` if `py -3.12` is missing |
| venv | `.venv` + `pip install -r requirements.txt` |
| Ollama | installs Ollama and pulls `qwen2.5:7b` (~4.7 GB). Use `-SkipOllama` if you use GitHub Models |
| Jaeger | downloads Jaeger 2.x into `tools\jaeger\jaeger.exe` (single binary, in‑memory store) |
| `.env` | created from `.env.example` with a random `SESSION_SECRET` |

**No GPU / slow VM?** Use the GitHub Models free tier instead of Ollama:

```ini
# .env
LLM_PROVIDER=github
GITHUB_TOKEN=<fine-grained PAT with "Models: read">
GITHUB_MODELS_MODEL=openai/gpt-4.1-mini
```

## 2. Run

```powershell
.\scripts\run.ps1
```

- Agent UI: <http://localhost:8000>
- Jaeger UI: <http://localhost:16686>

Try the suggestion chips: **San Francisco weather**, **Geocode Redmond address**, **Dallas weather**, **Paris**.
Under each answer the UI shows which tools ran and whether telemetry was exported.

## 3. Verify without a browser

```powershell
.\.venv\Scripts\python -m agent.smoke          # one full turn through the FastAPI app
.\.venv\Scripts\python -m agent.verify_spans   # shows which spans the Agent 365 exporter would accept
```

Expected `verify_spans` output:

```
Total spans: 4   exportable to Agent 365: 4
  SEND  chat           Chat qwen2.5:7b
  SEND  execute_tool   execute_tool get_weather
  SEND  chat           Chat qwen2.5:7b
  SEND  invoke_agent   invoke_agent {{AGENT_NAME}}
Root invoke_agent present: True
```

## 4. See the trace in Jaeger

1. Open <http://localhost:16686>, choose service **{{AGENT_NAME}}**, then **Find Traces**.
2. Open a trace. You should see one root `invoke_agent` span with `Chat …` (LLM inference) and
   `execute_tool …` children.
3. Click a span and point out the attributes that Agent 365 uses:

| Attribute | Meaning |
|---|---|
| `gen_ai.operation.name` | `invoke_agent`, `chat` or `execute_tool` |
| `gen_ai.agent.id` / `gen_ai.agent.name` | agent identity app ID / display name (empty until registered) |
| `gen_ai.conversation.id` | conversation, stable across turns |
| `user.id`, `user.email`, `client.address` | the caller (from the Entra sign‑in once auth is enabled) |
| `gen_ai.input.messages` / `gen_ai.output.messages` | prompt and response content |
| `gen_ai.tool.name`, `gen_ai.tool.call.arguments` | which tool ran, and with what arguments |

These are the same spans that `docs/03-observability.md` sends to Agent 365.

## 5. Diagnostics tab

Sign in, then open the **Diagnostics** tab next to **Chat** and click **Refresh**. It calls
`GET /api/diagnostics`, which returns 401 if you haven't signed in. The tab has these cards:

| Card | Shows |
|---|---|
| Signed-in user | Entra ID token claims: UPN, oid, tenant, and when you signed in |
| Agent identity | agent identity SP (`agentIdentity`), blueprint, sponsors, owners, custom security attributes |
| Token chain | user token (Tc), blueprint T1, **observability** OBO, **Purview** OBO, **MCP** OBO (or *not configured*), each with audience, scopes, expiry and a fingerprint |
| Permissions | blueprint inheritable permissions, delegated grants (agent + blueprint SP), app roles |
| Conditional Access | every CA policy, with whether it applies to the **agent identity** or the **user sign-in**, plus its grant controls and conditions |
| Purview policies | `protectionScopes/compute` result: activities, execution mode (inline/offline) and scope for the user |

Notes:
- The page shows **decoded claims only**. Raw tokens never leave the server.
- Directory and CA data need `az login` on the machine running the app, as a user who can read
  CA policies (e.g. Security Reader). The agent's own tokens can't read the directory.
  Set `DIAG_ADMIN_GRAPH=false` to turn this off. When it's on, any signed-in user of the app can see the tenant's CA policies, so leave it on only for local demos.
- CA evaluation is **static**: it matches assignments (users, groups, roles, agents, apps). It does not
  evaluate device, location or risk conditions, which Entra checks at sign-in. Use it to explain *which* policies
  target the agent, and use Entra sign-in logs as proof.
- Set `MCP_SCOPE` to the scope of an MCP server to show that OBO token as well.

## How the code is wired (for technical audiences)

- `agent/app.py`, per chat turn: `BaggageBuilder` (tenant, agent, conversation) → `InvokeAgentScope`
  (root span, with `CallerDetails` for the signed‑in user) → `prompt_guard()` (Purview SDK `processContent`, uploadText) →
  LangGraph run → `response_guard()` (Purview SDK `processContent`, downloadText). See `docs/05-phase2-dlp-ca.md`.
- `agent/a365_callbacks.py`: a LangChain callback opens an `InferenceScope` for each LLM call and an
  `ExecuteToolScope` for each tool call, parented explicitly to the invoke span.
- `agent/telemetry.py`: `use_microsoft_opentelemetry(enable_a365=True, …)` adds the Agent 365 exporter.
  A second `BatchSpanProcessor` sends the same spans to Jaeger over OTLP/HTTP.
  Generic LangChain auto‑instrumentation is switched off because it emits an `invoke_agent` span for
  every internal runnable, which pollutes the agent views.

## Troubleshooting

| Symptom | Fix |
|---|---|
| `Connection refused :11434` | Ollama isn't running. `run.ps1` starts it; or run `ollama serve` |
| First answer takes 30 s+ | The model is loading into memory; later turns are faster |
| Model answers without calling a tool | Ask more explicitly ("use the weather tool"), or switch to GitHub Models |
| No service in Jaeger | Check `OTLP_LOCAL_ENDPOINT=http://localhost:4318/v1/traces` and `tools\jaeger\jaeger.err.log` |
| Jaeger REST API `/api/services` returns 404 | Jaeger v2 serves `/api/v3/services`. The UI is unaffected |
| `[Errno 10048] … only one usage of each socket address` | An earlier copy of the app still holds port 8000. `Get-NetTCPConnection -LocalPort 8000 -State Listen` then `Stop-Process -Id <OwningProcess>` |
