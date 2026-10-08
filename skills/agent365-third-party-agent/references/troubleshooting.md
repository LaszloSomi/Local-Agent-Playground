# Troubleshooting

| Symptom | Cause | Fix |
|---|---|---|
| Jaeger shows traces but Defender, Purview and MAC are empty | Exporter off or placeholder IDs. Jaeger is independent | `Test-Readiness.ps1 -Tenant`, run `Configure-Entra.ps1`, restart, `A365_VERBOSE=true` |
| HTTP 200 but nothing appears | No **assigned** E7/Agent 365 license (`tenant_not_licensed`) | Assign a license to at least one user |
| Inference spans missing in cloud | `gen_ai.operation.name="Chat"` | Force lowercase `chat` (already in the template callbacks) |
| Lots of duplicate `invoke_agent` spans | LangChain auto-instrumentation | Disable it in `instrumentation_options` |
| `AADSTS500133` then endless exporter 401s | Expired user token reused for OBO | Silent refresh, and a resolver that returns None when expired. The user signs in again |
| `AADSTS65001` on Purview or OBO | Missing consent or inheritance for that scope | `a365 setup permissions custom …`, admin consent |
| Other users fail OtelWrite while the admin works | CLI grant is `Principal`-only | `Configure-Entra.ps1` (AllPrincipals) or inheritance |
| `/api/chat` returns 401 after a restart | In-memory sessions lost | Sign in again |
| Purview shows rows but no prompt text | Viewer lacks the AI content role, or it's an `execute_tool` row | Add *Data Security AI Content Viewers*, wait, sign out and in. Open the `invoke_agent` or inference row |
| "Related activity not found" | Tool rows have no content by design | Expected |
| Agent missing from Purview Activity explorer | Looking at the classic DLP explorer | DSPM → Activity explorer → **AI activities** |
| DLP never blocks; status `offline-sent` | Policy created in the portal, or location mismatch | Create it with `New-DlpComplianceRule` in PowerShell. `applicationLocation` must equal the policy location appId |
| `no-scope-activity-logged` | No DLP policy scopes this user or app yet | Wait for propagation; check the policy users and location |
| `Invalid URI: The hostname could not be parsed` in a script | PowerShell `$g`/`$G` collision | Use unique variable names |
| `?$select was unexpected at this time` | `az rest` through cmd on Windows | Use `Invoke-RestMethod` with an az token |
| `Request_UnsupportedQuery` on grants | Filtering on consentType or scope | Filter by clientId, resourceId or principalId only |
| `A window handle must be configured` | WAM in a headless terminal | Run in a real console window or terminal canvas |
| `UnicodeEncodeError` printing in Python | cp1252 console | `$env:PYTHONIOENCODING='utf-8'` |
| Port 8000 busy | Orphaned app process | `Get-NetTCPConnection -LocalPort 8000 -State Listen`, then stop that PID |
| Ollama slow or tools never called | Small model, no tool-calling prompt | Keep "Always call a tool", use `qwen2.5:7b` or larger, or GitHub Models |
| `verify_spans` reports 0 spans | AUTH_ENABLED=true and no session | It forces auth off; run tests with `AUTH_ENABLED=false` |
