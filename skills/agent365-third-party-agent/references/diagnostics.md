# Diagnostics tab

`GET /api/diagnostics` (`agent/diagnostics.py`) is rendered by the Diagnostics tab in `web/index.html`. It returns 401 when the user isn't signed in.

| Card | Source |
|---|---|
| Signed-in user | `state["id_claims"]` (ID token claims minus nonce, aio, rh and uti) |
| Token chain | User Tc (`fresh_user_token`), blueprint T1 (`_blueprint_t1`), observability OBO, Purview OBO, MCP OBO (only when `MCP_SCOPE` is set; otherwise "not-configured"). `token_summary()` shows aud, iss, appid/azp, idtyp, oid, upn, scp, roles, expiry, minutes left and a SHA-256 fingerprint. **Raw tokens are never returned** |
| Agent identity and permissions | Graph using the operator's **`az login`** token: agent SP (`/beta/servicePrincipals/{id}/microsoft.graph.agentIdentity`), sponsors, owners, blueprint, inheritable permissions (`/beta/applications/{bp}/microsoft.graph.agentIdentityBlueprint/inheritablePermissions`), grants (agent SP and blueprint SP), app roles, web client, custom security attributes, user memberOf |
| Conditional Access | `/beta/identity/conditionalAccess/policies`, statically evaluated for "agent identity" and "user sign-in". Applicable policies are sorted first |
| Purview | `purview.compute_scopes(force=True)`: modes per activity |

Settings:
- `DIAG_ADMIN_GRAPH=true` (the default) uses `az account get-access-token --resource-type ms-graph`. Every signed-in user of the app
  then sees the tenant's CA policies. That's fine for a localhost demo; turn it off otherwise.
- `MCP_SCOPE`: the OBO scope of an MCP server the agent calls.

The agent's own tokens can't read the directory or CA, which is why the az token is used. Graph calls run in parallel threads
(about 2 to 3 seconds). Each failure is reported per section as `{"_error": …}`, not as a 500.
