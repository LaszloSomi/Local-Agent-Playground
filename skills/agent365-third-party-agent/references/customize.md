# Customizing the template for a new agent

The template's domain is "WeatherGeo": a weather and geocoding agent using Open-Meteo and Nominatim. Everything except
the files below is domain-neutral plumbing. Change the domain, keep the plumbing.

## 1. Tools: `agent/tools.py`

- Expose a module-level `ALL_TOOLS = [...]` of LangChain `@tool` functions that return **JSON strings**.
- The docstring is the tool description the LLM sees. Make it specific and include an example input.
- Prefer keyless, reliable public APIs (Open-Meteo, Nominatim with a `User-Agent`, REST Countries, Frankfurter FX,
  NHTSA vPIC, USGS earthquakes, the public holidays API) or pure local logic (unit conversion, calculators, a small
  bundled JSON catalog). Live demos fail on API keys and rate limits.
- Set `timeout=` on every HTTP call. Return `{"error": ...}` JSON rather than raising, so the agent can explain the failure.
- Tool arguments and results are recorded on `execute_tool` spans (`gen_ai.tool.call.arguments/result`). Don't
  return secrets or personal data you wouldn't want in Defender and Purview.

## 2. Prompt: `agent/graph.py`

Replace `SYSTEM_PROMPT`. Keep the line "Always call a tool rather than guessing". Small local models (qwen2.5:7b)
otherwise hallucinate answers, and you get no `execute_tool` span to show.

## 3. Identity strings

`new_agent.py` already replaced `{{AGENT_NAME}}` and `{{PROVIDER_NAME}}`. Also review:
- `.env.example`: `AGENT365_AGENT_DESCRIPTION`.
- `web/index.html`: the header emoji (🌦️), placeholder text and example chips.
- `agent/tools.py`: `USER_AGENT` if you keep Nominatim.

## 4. DLP demo values

The template blocks prompts and responses containing the keyword "Dallas" using a custom SIT named
`Demo - Restricted City`. Pick an allowed/blocked pair that fits the new domain. Then update:
- The SIT name and keyword (Purview portal → Data classification → Sensitive info types), then
  `New-PurviewDlpDemo.ps1 -SensitiveInfoType '<SIT name>' -PolicyName '<policy name>'`.
- The prompts in `docs/05-phase2-dlp-ca.md`, `docs/presenter-script.md` and `README.md`.

## 5. Tests

`tests/test_observability.py` mocks `run_agent` or the LLM, so it doesn't depend on the domain. If you assert on tool
names, update `get_weather`/`geocode` there. Add a unit test per new tool, with HTTP mocked, for anything non-trivial.

## 6. Docs and diagrams

Search the repo for these terms and rewrite them for the new domain: `weather`, `Weather`, `geocod`, `San Francisco`,
`Dallas`, `Open-Meteo`, `WeatherGeo`. Keep the structure:
`01-run-agent` → `02-register` → `03-observability` → `04-reset` → `05-phase2-dlp-ca` → `presenter-script`.
`docs/00-diagrams.html` renders mermaid diagrams from `<pre class="mermaid">` blocks. Update labels, keep the theming code,
and check it in a browser: no "Syntax error" boxes.

## 7. Quick validation

```powershell
.\.venv\Scripts\python.exe -m agent.smoke        # one turn, no browser
$env:AUTH_ENABLED='false'; $env:ENABLE_A365_OBSERVABILITY_EXPORTER='true'
.\.venv\Scripts\python.exe -m unittest discover tests
.\.venv\Scripts\python.exe -m agent.verify_spans # spans pass the Agent 365 exporter filters
```
